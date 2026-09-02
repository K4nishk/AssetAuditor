"""HTTP-layer tests for `DELETE /api/account` (KCH-45 / AA-10).

Follows tests/api/test_uploads.py's approach: `TestClient` + dependency
overrides for auth, a fake standing in for the RLS-scoped connection
(`app.routes.account` calls `rls_connection` directly rather than through a
`Depends`-wrapped `_conn` — see that module's docstring for why), and fakes
for the Blob/Auth-Admin clients. The row-purge SQL itself is covered by
tests/db/test_account_lifecycle_live.py (skips here, no live Postgres) and
tests/unit/test_account_purge.py (fake-connection call-order proof).
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_claims
from app.main import app
from app.routes import account as account_module

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class FakeConnection:
    def __init__(self, *, fetchrow_results, fetch_results):
        self._fetchrow_results = list(fetchrow_results)
        self._fetch_results = list(fetch_results)

    async def fetchrow(self, query, *args):
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        return self._fetch_results.pop(0)


class FakeBlob:
    def __init__(self):
        self.deleted_urls: list[str] = []
        self.deleted_prefixes: list[str] = []

    def delete(self, url):
        self.deleted_urls.append(url)

    def delete_prefix(self, prefix):
        self.deleted_prefixes.append(prefix)
        return 0


class FakeAuthAdmin:
    def __init__(self):
        self.deleted_user_ids: list[str] = []

    def delete_user(self, user_id):
        self.deleted_user_ids.append(user_id)


def _fresh_claims(iat_offset_seconds: float = 0.0) -> dict:
    return {"sub": USER_ID, "iat": time.time() - iat_offset_seconds}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.pop(get_current_claims, None)


def _override_claims(claims: dict) -> None:
    app.dependency_overrides[get_current_claims] = lambda: claims


def _successful_conn() -> FakeConnection:
    return FakeConnection(
        fetchrow_results=[{"id": "le-start"}, {"id": "le-complete"}],
        fetch_results=[
            [],  # redact_lineage_events
            [],  # unpurged_bronze_blob_urls
            [{"id": "a1"}],  # accounts
            [{"id": "b1"}],  # bronze_files
            [],  # liabilities
            [],  # room_events
            [],  # networth_snapshots
            [],  # term_buckets
            [],  # diversification_cuts
            [{"id": USER_ID}],  # users_profile
        ],
    )


def test_delete_account_purges_and_returns_a_summary(monkeypatch):
    conn = _successful_conn()

    @asynccontextmanager
    async def fake_rls_connection(user_id):
        assert user_id == USER_ID
        yield conn

    fake_blob = FakeBlob()
    fake_auth_admin = FakeAuthAdmin()
    monkeypatch.setattr(account_module, "rls_connection", fake_rls_connection)
    monkeypatch.setattr(account_module, "get_blob_storage", lambda: fake_blob)
    monkeypatch.setattr(account_module, "get_auth_admin_client", lambda: fake_auth_admin)
    _override_claims(_fresh_claims())

    response = client.delete("/api/account")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "purged"
    assert body["accounts_purged"] == 1
    assert body["bronze_files_purged"] == 1
    assert fake_auth_admin.deleted_user_ids == [USER_ID]
    assert fake_blob.deleted_prefixes == [
        f"bronze/{USER_ID}/",
        f"silver/{USER_ID}/",
        f"gold/{USER_ID}/",
    ]


def test_delete_account_rejects_a_stale_token_without_purging(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fake_rls_connection(user_id):
        calls.append(user_id)
        yield _successful_conn()

    monkeypatch.setattr(account_module, "rls_connection", fake_rls_connection)
    monkeypatch.setattr(account_module, "get_blob_storage", lambda: FakeBlob())
    monkeypatch.setattr(account_module, "get_auth_admin_client", lambda: FakeAuthAdmin())
    _override_claims(_fresh_claims(iat_offset_seconds=3600))  # signed in an hour ago

    response = client.delete("/api/account")

    assert response.status_code == 403
    assert calls == []  # never touched the DB


def test_delete_account_requires_auth():
    response = client.delete("/api/account")

    assert response.status_code == 401


def test_delete_account_401s_on_a_token_missing_iat(monkeypatch):
    monkeypatch.setattr(account_module, "get_blob_storage", lambda: FakeBlob())
    monkeypatch.setattr(account_module, "get_auth_admin_client", lambda: FakeAuthAdmin())
    _override_claims({"sub": USER_ID})  # no iat claim

    response = client.delete("/api/account")

    assert response.status_code == 401
