"""HTTP-layer tests for the demo-mode routes (KCH-69 / AA-32).

Same approach as `tests/api/test_manual_entry_routes.py`: `TestClient` +
dependency overrides, with a fake connection standing in for a live
Postgres. The full seed pipeline (purge -> 7 fixtures -> confirm -> silver
-> `rebuild_gold`) makes a call-count-accurate `FakeConnection` impractical
to hand-roll here (`rebuild_gold` alone issues a variable number of queries
depending on what silver holds) — that end-to-end proof is
`tests/db/test_demo_seed_live.py` against a real ephemeral Postgres instead.
This file covers what the route layer owns on its own: the `DEMO_USER_ID`
gate (the "never touches real data" guarantee) and `GET /status`, both of
which resolve before any DB call happens.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import demo as demo_module

DEMO_USER_ID = "00000000-0000-0000-0000-0000000000de"
OTHER_USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class _UnusedConnection:
    """`_require_demo_user` must reject before any query runs — a connection
    that raises on first use makes that ordering an assertion, not a hope."""

    async def fetch(self, *args, **kwargs):
        raise AssertionError("no DB query should run before the demo-user gate")

    fetchrow = fetch
    fetchval = fetch
    execute = fetch


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(demo_module._conn, None)


def _sign_in_as(user_id: str) -> None:
    app.dependency_overrides[get_current_user_id] = lambda: user_id


def _block_db() -> None:
    async def _fake_conn():
        yield _UnusedConnection()

    app.dependency_overrides[demo_module._conn] = _fake_conn


# --- GET /api/demo/status -----------------------------------------------------


def test_status_reports_unconfigured_when_demo_user_id_is_unset(monkeypatch):
    monkeypatch.delenv("DEMO_USER_ID", raising=False)
    _sign_in_as(OTHER_USER_ID)

    response = client.get("/api/demo/status")

    assert response.status_code == 200
    assert response.json() == {"configured": False, "is_demo_user": False}


def test_status_reports_is_demo_user_true_for_the_demo_account(monkeypatch):
    monkeypatch.setenv("DEMO_USER_ID", DEMO_USER_ID)
    _sign_in_as(DEMO_USER_ID)

    response = client.get("/api/demo/status")

    assert response.status_code == 200
    assert response.json() == {"configured": True, "is_demo_user": True}


def test_status_reports_is_demo_user_false_for_a_real_account(monkeypatch):
    monkeypatch.setenv("DEMO_USER_ID", DEMO_USER_ID)
    _sign_in_as(OTHER_USER_ID)

    response = client.get("/api/demo/status")

    assert response.status_code == 200
    assert response.json() == {"configured": True, "is_demo_user": False}


def test_status_requires_auth():
    response = client.get("/api/demo/status")

    assert response.status_code == 401


# --- POST /api/demo/seed: the DEMO_USER_ID gate --------------------------------


def test_seed_requires_auth():
    response = client.post("/api/demo/seed")

    assert response.status_code == 401


def test_seed_returns_503_when_demo_mode_is_not_configured(monkeypatch):
    monkeypatch.delenv("DEMO_USER_ID", raising=False)
    _sign_in_as(OTHER_USER_ID)
    _block_db()

    response = client.post("/api/demo/seed")

    assert response.status_code == 503


def test_seed_rejects_a_real_account_even_with_a_valid_jwt(monkeypatch):
    # This is the "never touches real data" guarantee: any authenticated
    # caller other than the configured demo account is refused outright,
    # before a single row is read or written.
    monkeypatch.setenv("DEMO_USER_ID", DEMO_USER_ID)
    _sign_in_as(OTHER_USER_ID)
    _block_db()

    response = client.post("/api/demo/seed")

    assert response.status_code == 403


def test_seed_never_touches_the_db_for_a_rejected_caller(monkeypatch):
    monkeypatch.setenv("DEMO_USER_ID", DEMO_USER_ID)
    _sign_in_as(OTHER_USER_ID)
    _block_db()  # _UnusedConnection asserts if any query is attempted

    response = client.post("/api/demo/seed")

    assert response.status_code == 403
