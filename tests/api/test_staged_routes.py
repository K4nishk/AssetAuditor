"""HTTP-layer tests for the parse-confirm routes (KCH-52 / AA-17).

Follows tests/api/test_uploads.py's approach: exercise routing, status codes,
and auth/state gating via `TestClient` + dependency overrides, with the
query-layer functions monkeypatched rather than a live Postgres — the SQL
wrappers and the silver FK-resolution logic have their own coverage in
tests/db/test_staged_rows_confirm.py and tests/unit/test_staged_rows_domain.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.db.queries.silver import SilverResolutionError
from app.main import app
from app.routes import staged as staged_module

USER_ID = "00000000-0000-0000-0000-000000000042"
JOB_ID = "00000000-0000-0000-0000-00000000000a"
ROW_ID = "00000000-0000-0000-0000-00000000000b"

client = TestClient(app)


class FakeConnection:
    """Only ever used here for `LineageEmitter`'s own inserts — every other
    query the routes make goes through a monkeypatched module function."""

    async def fetchrow(self, query, *args):
        return None


def _job(status: str) -> dict:
    return {
        "id": JOB_ID,
        "user_id": USER_ID,
        "bronze_file_id": "bronze-1",
        "status": status,
        "claimed_by": None,
        "claimed_at": None,
        "error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def _row(**overrides) -> dict:
    row = {
        "id": ROW_ID,
        "user_id": USER_ID,
        "job_id": JOB_ID,
        "entity": "transaction",
        "payload": {"amount": "10.00"},
        "confidence": 1.0,
        "method": "deterministic",
        "confirmed_at": None,
        "deactivated_at": None,
        "created_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture(autouse=True)
def _conn_override():
    async def _fake_conn():
        yield FakeConnection()

    app.dependency_overrides[staged_module._conn] = _fake_conn
    yield
    app.dependency_overrides.pop(staged_module._conn, None)


# --- GET /api/staged/{job_id}/rows -------------------------------------------


def test_list_staged_rows_flags_low_confidence(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("needs_user")

    async def _list_rows(conn, *, user_id, job_id, unconfirmed_only=False):
        return [_row(confidence=0.4), _row(id="row-2", confidence=1.0)]

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)
    monkeypatch.setattr(staged_module.staged_rows, "list_rows_for_job", _list_rows)

    response = client.get(f"/api/staged/{JOB_ID}/rows")

    assert response.status_code == 200
    body = response.json()
    assert body["job_status"] == "needs_user"
    assert [row["is_low_confidence"] for row in body["rows"]] == [True, False]


def test_list_staged_rows_404s_for_an_unknown_job(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return None

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)

    response = client.get(f"/api/staged/{JOB_ID}/rows")

    assert response.status_code == 404


def test_list_staged_rows_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get(f"/api/staged/{JOB_ID}/rows")

    assert response.status_code == 401


# --- PATCH /api/staged/{job_id}/rows/{row_id} --------------------------------


def test_edit_staged_row_applies_a_manual_correction(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("needs_user")

    async def _update(conn, *, user_id, job_id, row_id, payload):
        return _row(payload=payload, method="manual_correction", confidence=1.0)

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)
    monkeypatch.setattr(staged_module.staged_rows, "update_row_payload", _update)

    response = client.patch(
        f"/api/staged/{JOB_ID}/rows/{ROW_ID}", json={"payload": {"amount": "12.34"}}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["method"] == "manual_correction"
    assert body["payload"] == {"amount": "12.34"}
    assert body["is_low_confidence"] is False


def test_edit_staged_row_rejects_an_empty_payload(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("needs_user")

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)

    response = client.patch(f"/api/staged/{JOB_ID}/rows/{ROW_ID}", json={"payload": {}})

    assert response.status_code == 422


def test_edit_staged_row_409s_once_the_job_is_done(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("done")

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)

    response = client.patch(
        f"/api/staged/{JOB_ID}/rows/{ROW_ID}", json={"payload": {"amount": "1.00"}}
    )

    assert response.status_code == 409


def test_edit_staged_row_404s_when_already_confirmed(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("needs_user")

    async def _update(conn, *, user_id, job_id, row_id, payload):
        return None

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)
    monkeypatch.setattr(staged_module.staged_rows, "update_row_payload", _update)

    response = client.patch(
        f"/api/staged/{JOB_ID}/rows/{ROW_ID}", json={"payload": {"amount": "1.00"}}
    )

    assert response.status_code == 404


# --- POST /api/staged/{job_id}/confirm ---------------------------------------


def _patch_confirm_happy_path(monkeypatch, *, rows):
    async def _get_job(conn, *, user_id, job_id):
        return _job("needs_user")

    async def _list_rows(conn, *, user_id, job_id, unconfirmed_only=False):
        return rows

    async def _find_run_id(conn, *, user_id, job_id):
        return None

    async def _mark_confirmed(conn, *, user_id, job_id, row_ids):
        return None

    async def _mark_done(conn, *, user_id, job_id):
        return _job("done")

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)
    monkeypatch.setattr(staged_module.staged_rows, "list_rows_for_job", _list_rows)
    monkeypatch.setattr(staged_module.staged_rows, "find_run_id_for_job", _find_run_id)
    monkeypatch.setattr(staged_module.staged_rows, "mark_confirmed", _mark_confirmed)
    monkeypatch.setattr(staged_module.etl_jobs, "mark_job_done", _mark_done)


def test_confirm_writes_silver_and_marks_the_job_done(monkeypatch):
    rows = [_row(entity="account")]
    _patch_confirm_happy_path(monkeypatch, rows=rows)

    async def _write(conn, *, user_id, rows):
        return {"account": 1, "holding": 0, "lot": 0, "transaction": 0, "liability": 0}

    monkeypatch.setattr(staged_module, "write_confirmed_rows", _write)

    response = client.post(f"/api/staged/{JOB_ID}/confirm")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["confirmed_row_count"] == 1
    assert body["silver_write_summary"]["account"] == 1


def test_confirm_409s_when_job_is_not_awaiting_confirmation(monkeypatch):
    async def _get_job(conn, *, user_id, job_id):
        return _job("parsing")

    monkeypatch.setattr(staged_module.etl_jobs, "get_job", _get_job)

    response = client.post(f"/api/staged/{JOB_ID}/confirm")

    assert response.status_code == 409


def test_confirm_returns_422_when_a_row_cannot_resolve(monkeypatch):
    rows = [_row(entity="holding")]
    _patch_confirm_happy_path(monkeypatch, rows=rows)

    async def _write(conn, *, user_id, rows):
        raise SilverResolutionError("holding references account_mask='x' with no matching account")

    monkeypatch.setattr(staged_module, "write_confirmed_rows", _write)

    response = client.post(f"/api/staged/{JOB_ID}/confirm")

    assert response.status_code == 422
    assert "account_mask" in response.json()["detail"]


def test_confirm_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.post(f"/api/staged/{JOB_ID}/confirm")

    assert response.status_code == 401
