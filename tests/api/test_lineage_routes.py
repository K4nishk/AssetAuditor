"""HTTP-layer tests for the drill-down route (KCH-60 / AA-23).

Same fake-connection + monkeypatched-query-module approach as
tests/api/test_staged_routes.py: the SQL wrappers themselves get their own
coverage in tests/db/test_lineage_slice_live.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import lineage as lineage_module

USER_ID = "00000000-0000-0000-0000-000000000042"
JOB_ID = "00000000-0000-0000-0000-00000000000a"
BRONZE_FILE_ID = "00000000-0000-0000-0000-00000000000b"
RUN_ID = str(uuid.uuid4())
SNAPSHOT_DATE = date(2026, 7, 31)

client = TestClient(app)


class FakeConnection:
    """Unused directly — every query goes through a monkeypatched module
    function, same convention as tests/api/test_staged_routes.py."""


def _job() -> dict:
    return {
        "id": JOB_ID,
        "user_id": USER_ID,
        "bronze_file_id": BRONZE_FILE_ID,
        "status": "done",
        "claimed_by": None,
        "claimed_at": None,
        "error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def _bronze_file(**overrides) -> dict:
    row = {
        "id": BRONZE_FILE_ID,
        "institution": "questrade",
        "period": "2026-06",
        "blob_url": "https://blob.example/bronze/x",
        "purged_at": None,
    }
    row.update(overrides)
    return row


def _staged_row(**overrides) -> dict:
    row = {
        "id": "00000000-0000-0000-0000-00000000000c",
        "entity": "transaction",
        "payload": {"amount": "10.00", "kind": "buy"},
        "method": "deterministic",
        "confirmed_at": datetime(2026, 7, 1, tzinfo=UTC),
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

    app.dependency_overrides[lineage_module._conn] = _fake_conn
    yield
    app.dependency_overrides.pop(lineage_module._conn, None)


def _patch_full_chain(monkeypatch, *, run_id=RUN_ID, job_id=JOB_ID, bronze=None, rows=None):
    async def _find_term_bucket_run_id(conn, *, user_id, snapshot_date, bucket):
        return run_id

    async def _find_diversification_run_id(conn, *, user_id, snapshot_date, cut, label):
        return run_id

    async def _find_net_worth_run_id(conn, *, user_id, snapshot_date):
        return run_id

    async def _find_job_id(conn, *, user_id, run_id):
        return job_id

    async def _get_job(conn, *, user_id, job_id):
        return _job() if job_id is not None else None

    async def _get_bronze_file(conn, *, user_id, bronze_file_id):
        return bronze if bronze is not None else _bronze_file()

    async def _list_rows(conn, *, user_id, job_id, unconfirmed_only=False):
        return rows if rows is not None else [_staged_row()]

    monkeypatch.setattr(
        lineage_module.lineage_slice_queries,
        "find_run_id_for_term_bucket",
        _find_term_bucket_run_id,
    )
    monkeypatch.setattr(
        lineage_module.lineage_slice_queries,
        "find_run_id_for_diversification_cut",
        _find_diversification_run_id,
    )
    monkeypatch.setattr(
        lineage_module.lineage_slice_queries, "find_run_id_for_net_worth", _find_net_worth_run_id
    )
    monkeypatch.setattr(lineage_module.lineage_slice_queries, "find_job_id_for_run", _find_job_id)
    monkeypatch.setattr(lineage_module.lineage_slice_queries, "get_bronze_file", _get_bronze_file)
    monkeypatch.setattr(lineage_module.etl_jobs_queries, "get_job", _get_job)
    monkeypatch.setattr(lineage_module.staged_rows_queries, "list_rows_for_job", _list_rows)


# --- term_bucket --------------------------------------------------------------


def test_term_bucket_slice_returns_run_source_file_and_rows(monkeypatch):
    _patch_full_chain(monkeypatch)

    response = client.get(
        "/api/lineage/slice",
        params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE), "bucket": "short_term"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["job_id"] == JOB_ID
    assert body["source_file"]["institution"] == "questrade"
    assert body["source_file"]["is_purged"] is False
    assert body["source_file"]["blob_url"] == "https://blob.example/bronze/x"
    assert len(body["rows"]) == 1
    assert body["rows"][0]["method"] == "deterministic"
    assert body["rows"][0]["confirmed_at"] is not None


def test_term_bucket_slice_requires_bucket(monkeypatch):
    _patch_full_chain(monkeypatch)

    response = client.get(
        "/api/lineage/slice", params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE)}
    )

    assert response.status_code == 422


# --- diversification -----------------------------------------------------------


def test_diversification_slice_requires_cut_and_label(monkeypatch):
    _patch_full_chain(monkeypatch)

    response = client.get(
        "/api/lineage/slice",
        params={
            "kind": "diversification",
            "snapshot_date": str(SNAPSHOT_DATE),
            "cut": "institution",
        },
    )

    assert response.status_code == 422


def test_diversification_slice_happy_path(monkeypatch):
    _patch_full_chain(monkeypatch)

    response = client.get(
        "/api/lineage/slice",
        params={
            "kind": "diversification",
            "snapshot_date": str(SNAPSHOT_DATE),
            "cut": "institution",
            "label": "questrade",
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN_ID


# --- net_worth -------------------------------------------------------------


def test_net_worth_slice_happy_path(monkeypatch):
    _patch_full_chain(monkeypatch)

    response = client.get(
        "/api/lineage/slice", params={"kind": "net_worth", "snapshot_date": str(SNAPSHOT_DATE)}
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN_ID


# --- gaps: no gold row, job-less run, purged source file ------------------


def test_unknown_slice_404s(monkeypatch):
    _patch_full_chain(monkeypatch, run_id=None)

    response = client.get(
        "/api/lineage/slice",
        params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE), "bucket": "short_term"},
    )

    assert response.status_code == 404


def test_run_with_no_bound_job_returns_empty_rows_not_a_404(monkeypatch):
    """A gold rebuild's own lineage events may carry no job_id (`worker.gold
    .rebuild_gold`'s deferred-wiring gap) — the run itself is real, so this
    is a 200 with nothing further to walk, not a 404."""
    _patch_full_chain(monkeypatch, job_id=None)

    response = client.get(
        "/api/lineage/slice",
        params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE), "bucket": "short_term"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == RUN_ID
    assert body["job_id"] is None
    assert body["source_file"] is None
    assert body["rows"] == []


def test_purged_source_file_reports_a_tombstone_without_a_blob_url(monkeypatch):
    _patch_full_chain(
        monkeypatch,
        bronze=_bronze_file(blob_url="", purged_at=datetime(2026, 8, 1, tzinfo=UTC)),
    )

    response = client.get(
        "/api/lineage/slice",
        params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE), "bucket": "short_term"},
    )

    assert response.status_code == 200
    source_file = response.json()["source_file"]
    assert source_file["is_purged"] is True
    assert source_file["blob_url"] is None
    assert source_file["purged_at"] is not None


def test_lineage_slice_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get(
        "/api/lineage/slice",
        params={"kind": "term_bucket", "snapshot_date": str(SNAPSHOT_DATE), "bucket": "short_term"},
    )

    assert response.status_code == 401
