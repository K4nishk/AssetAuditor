"""Unit tests for `app.account_purge` (KCH-45 / AA-10).

Same fake-connection approach as tests/unit/test_lineage_emitter.py/
tests/unit/test_worker_retention_sweep.py — proves call order, bind
arguments, and the returned summary without a live Postgres. Real-Postgres
cascade/redaction behaviour is tests/db/test_account_lifecycle_live.py,
which skips here per CLAUDE.md (no local Postgres in this sandbox).
"""

from __future__ import annotations

import pytest

from app.account_purge import purge_account_external, purge_account_rows

pytestmark = pytest.mark.asyncio

USER_ID = "00000000-0000-0000-0000-000000000042"


class FakeConnection:
    def __init__(self, *, fetchrow_results, fetch_results):
        self._fetchrow_results = list(fetchrow_results)
        self._fetch_results = list(fetch_results)
        self.fetchrow_calls: list[tuple] = []
        self.fetch_calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query, args))
        return self._fetchrow_results.pop(0)

    async def fetch(self, query, *args):
        self.fetch_calls.append((query, args))
        return self._fetch_results.pop(0)


def _conn():
    return FakeConnection(
        fetchrow_results=[{"id": "le-start"}, {"id": "le-complete"}],
        fetch_results=[
            [{"id": "le-1"}, {"id": "le-2"}],  # redact_lineage_events
            [{"blob_url": "https://blob.example/bronze/u/1"},
             {"blob_url": "https://blob.example/bronze/u/2"}],  # unpurged_bronze_blob_urls
            [{"id": "a1"}],  # accounts
            [{"id": "b1"}, {"id": "b2"}],  # bronze_files
            [],  # liabilities
            [{"id": "r1"}],  # room_events
            [],  # networth_snapshots
            [],  # term_buckets
            [],  # diversification_cuts
            [{"id": USER_ID}],  # users_profile
        ],
    )


async def test_purge_account_rows_returns_the_full_summary():
    conn = _conn()

    result = await purge_account_rows(conn, user_id=USER_ID)

    assert result.lineage_redacted == 2
    assert result.bronze_blob_urls == [
        "https://blob.example/bronze/u/1",
        "https://blob.example/bronze/u/2",
    ]
    assert result.row_counts.accounts == 1
    assert result.row_counts.bronze_files == 2
    assert result.row_counts.liabilities == 0
    assert result.row_counts.room_events == 1
    assert result.row_counts.networth_snapshots == 0
    assert result.row_counts.term_buckets == 0
    assert result.row_counts.diversification_cuts == 0
    assert result.row_counts.profile == 1
    assert len(result.run_id) == 36  # uuid4 string


async def test_purge_account_rows_emits_start_then_complete_under_one_run_id():
    conn = _conn()

    result = await purge_account_rows(conn, user_id=USER_ID)

    assert len(conn.fetchrow_calls) == 2
    start_args, complete_args = (call[1] for call in conn.fetchrow_calls)
    assert start_args[1] == complete_args[1] == result.run_id
    assert start_args[3] == "START"
    assert complete_args[3] == "COMPLETE"


async def test_purge_account_rows_redacts_lineage_before_purging_and_excludes_its_own_run():
    conn = _conn()

    result = await purge_account_rows(conn, user_id=USER_ID)

    redact_query, redact_args = conn.fetch_calls[0]
    assert "update public.lineage_events" in redact_query
    assert redact_args == (USER_ID, result.run_id)


async def test_purge_account_external_deletes_captured_urls_and_every_prefix():
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

    blob = FakeBlob()
    auth_admin = FakeAuthAdmin()

    purge_account_external(
        user_id=USER_ID,
        blob=blob,
        auth_admin=auth_admin,
        bronze_blob_urls=["https://blob.example/bronze/u/1", "https://blob.example/bronze/u/2"],
    )

    assert blob.deleted_urls == [
        "https://blob.example/bronze/u/1",
        "https://blob.example/bronze/u/2",
    ]
    assert blob.deleted_prefixes == [
        f"bronze/{USER_ID}/",
        f"silver/{USER_ID}/",
        f"gold/{USER_ID}/",
    ]
    assert auth_admin.deleted_user_ids == [USER_ID]


async def test_purge_account_external_deletes_the_auth_identity_last():
    calls: list[str] = []

    class FakeBlob:
        def delete(self, url):
            calls.append(f"delete:{url}")

        def delete_prefix(self, prefix):
            calls.append(f"delete_prefix:{prefix}")
            return 0

    class FakeAuthAdmin:
        def delete_user(self, user_id):
            calls.append(f"delete_user:{user_id}")

    purge_account_external(
        user_id=USER_ID,
        blob=FakeBlob(),
        auth_admin=FakeAuthAdmin(),
        bronze_blob_urls=["https://blob.example/bronze/u/1"],
    )

    assert calls[-1] == f"delete_user:{USER_ID}"
