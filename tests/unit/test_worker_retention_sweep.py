"""Unit tests for `worker.retention` (KCH-54 / AA-19).

Fake asyncpg-shaped connection + fake `BlobStorage`, same convention as
tests/unit/test_worker_heartbeat.py/test_worker_job_poll_loop.py — no live
Postgres needed for the sweep logic itself. Live-DB proof (real constraints,
real cascades) is tests/db/test_retention_sweep_live.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.uploads.blob import BlobDeleteError
from worker import metrics
from worker.retention import (
    BRONZE_RETENTION,
    JOB_LOG_RETENTION,
    run_retention_sweep,
    sweep_bronze_files,
    sweep_job_logs,
)

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 9, 2, tzinfo=UTC)


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeConnection:
    def __init__(self, *, bronze_rows=(), scrub_rows=()):
        self._bronze_rows = list(bronze_rows)
        self._scrub_rows = list(scrub_rows)
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return _FakeTransaction()

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if "bronze_files" in query:
            return self._bronze_rows
        if "etl_jobs" in query:
            return self._scrub_rows
        raise AssertionError(f"unexpected fetch query: {query}")

    async def execute(self, query: str, *args):
        self.execute_calls.append((query, args))

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        return {"id": "lineage-row-id"}


class FakeBlob:
    def __init__(self, *, fail_urls: frozenset[str] = frozenset()):
        self.deleted: list[str] = []
        self._fail_urls = fail_urls

    def put(self, pathname, data, content_type):  # pragma: no cover - unused
        raise NotImplementedError

    def delete(self, url: str) -> None:
        if url in self._fail_urls:
            raise BlobDeleteError(url)
        self.deleted.append(url)


def _bronze_row(**overrides):
    row = {
        "id": "bronze-1",
        "user_id": "user-1",
        "sha256": "a" * 64,
        "institution": "questrade",
        "blob_url": "https://blob.example/bronze/user-1/aaa",
        "purge_run_id": None,
    }
    row.update(overrides)
    return row


async def test_sweep_bronze_files_purges_a_file_past_the_retention_ttl():
    conn = FakeConnection(bronze_rows=[_bronze_row()])
    blob = FakeBlob()

    result = await sweep_bronze_files(conn, blob=blob, now=_NOW)

    assert result.purged == 1
    assert result.failed == 0
    assert blob.deleted == ["https://blob.example/bronze/user-1/aaa"]
    # phase 1 (pending marker) then phase 3 (finalize): purged_at marked,
    # blob_url tombstoned, outbox marker cleared
    assert len(conn.execute_calls) == 2
    pending_query, pending_args = conn.execute_calls[0]
    assert "purge_run_id" in pending_query
    assert pending_args[0] == "bronze-1"
    persisted_run_id = pending_args[1]
    assert conn.execute_calls[1][1] == ("bronze-1", _NOW)
    # one retention_purge START (phase 1) + one COMPLETE (phase 3)
    assert len(conn.fetchrow_calls) == 2
    start_args, complete_args = conn.fetchrow_calls
    assert start_args[1][1] == persisted_run_id
    assert start_args[1][3] == "START"
    assert complete_args[1][1] == persisted_run_id
    assert complete_args[1][3] == "COMPLETE"
    facets = json.loads(start_args[1][4])
    assert facets["sha256"] == "a" * 64
    assert facets["bronze_file_id"] == "bronze-1"


async def test_sweep_bronze_files_selects_with_the_14_day_cutoff():
    conn = FakeConnection(bronze_rows=[])
    blob = FakeBlob()

    await sweep_bronze_files(conn, blob=blob, now=_NOW)

    assert BRONZE_RETENTION == timedelta(days=14)
    query, args = conn.fetch_calls[0]
    assert args == (_NOW - BRONZE_RETENTION,)


async def test_sweep_bronze_files_leaves_a_row_unpurged_when_blob_delete_fails():
    row = _bronze_row()
    conn = FakeConnection(bronze_rows=[row])
    blob = FakeBlob(fail_urls=frozenset([row["blob_url"]]))

    result = await sweep_bronze_files(conn, blob=blob, now=_NOW)

    assert result.purged == 0
    assert result.failed == 1
    # the pending-purge marker and its START event are persisted *before*
    # blob.delete is attempted, so provenance of the attempt survives even
    # though the delete itself failed.
    assert len(conn.execute_calls) == 1
    assert "purge_run_id" in conn.execute_calls[0][0]
    assert len(conn.fetchrow_calls) == 1
    assert conn.fetchrow_calls[0][1][3] == "START"


async def test_sweep_bronze_files_continues_past_a_failing_row():
    ok_row = _bronze_row(id="bronze-ok", blob_url="https://blob.example/ok")
    failing_row = _bronze_row(id="bronze-fail", blob_url="https://blob.example/fail")
    conn = FakeConnection(bronze_rows=[failing_row, ok_row])
    blob = FakeBlob(fail_urls=frozenset([failing_row["blob_url"]]))

    result = await sweep_bronze_files(conn, blob=blob, now=_NOW)

    assert result.purged == 1
    assert result.failed == 1
    assert blob.deleted == [ok_row["blob_url"]]


async def test_sweep_bronze_files_keeps_provenance_when_finalize_fails_after_delete():
    """Regression for the CodeRabbit KCH-54 round-2 finding: a DB failure
    that lands *after* blob.delete succeeds (but before `purged_at` is set)
    must not lose the fact that a delete was attempted."""
    row = _bronze_row()

    class FailFinalizeConnection(FakeConnection):
        async def execute(self, query, *args):
            self.execute_calls.append((query, args))
            if "set purged_at" in query:
                raise RuntimeError("db exploded during finalize")

    conn = FailFinalizeConnection(bronze_rows=[row])
    blob = FakeBlob()

    with pytest.raises(RuntimeError):
        await sweep_bronze_files(conn, blob=blob, now=_NOW)

    # the blob really was deleted, and the finalize write was attempted (and
    # is what raised) — but only the pending-purge marker from *before* the
    # delete actually committed, since the finalize transaction rolled back.
    assert blob.deleted == [row["blob_url"]]
    assert len(conn.execute_calls) == 2
    pending_query, pending_args = conn.execute_calls[0]
    assert "purge_run_id" in pending_query
    assert pending_args[0] == row["id"]
    persisted_run_id = pending_args[1]
    assert "set purged_at" in conn.execute_calls[1][0]

    # only the START event (from the pending-marker transaction, which did
    # commit) is on record — the COMPLETE never got emitted.
    assert len(conn.fetchrow_calls) == 1
    _, start_args = conn.fetchrow_calls[0]
    assert start_args[1] == persisted_run_id
    assert start_args[3] == "START"


async def test_sweep_bronze_files_resumes_a_row_stuck_pending_from_a_prior_crash():
    """Next sweep after `..._keeps_provenance_when_finalize_fails_after_delete`:
    the row still carries its `purge_run_id`, so retrying must not persist a
    second marker or emit a duplicate START — only finalize + COMPLETE."""
    stuck_run_id = "11111111-1111-1111-1111-111111111111"
    row = _bronze_row(purge_run_id=stuck_run_id)
    conn = FakeConnection(bronze_rows=[row])
    blob = FakeBlob()

    result = await sweep_bronze_files(conn, blob=blob, now=_NOW)

    assert result.purged == 1
    assert result.failed == 0
    assert blob.deleted == [row["blob_url"]]
    assert len(conn.execute_calls) == 1
    assert conn.execute_calls[0][1] == (row["id"], _NOW)
    assert len(conn.fetchrow_calls) == 1
    _, complete_args = conn.fetchrow_calls[0]
    assert complete_args[1] == stuck_run_id
    assert complete_args[3] == "COMPLETE"


async def test_sweep_job_logs_scrubs_terminal_jobs_past_the_4_day_ttl():
    conn = FakeConnection(scrub_rows=[{"id": "job-1"}, {"id": "job-2"}])

    scrubbed = await sweep_job_logs(conn, now=_NOW)

    assert scrubbed == 2
    assert JOB_LOG_RETENTION == timedelta(days=4)
    # the scrub is an `update ... returning id`, so it rides `fetch`, not
    # `execute` — the returned ids are what the count comes from.
    assert conn.execute_calls == []
    query, args = conn.fetch_calls[0]
    assert args == (_NOW - JOB_LOG_RETENTION,)
    # it must null out `error` only, on terminal jobs only: dropping the status
    # filter would scrub live jobs, and widening the SET would delete the job
    # metadata that staged_rows/lineage_events hang off (see module docstring).
    assert "update public.etl_jobs" in query
    assert "set error = null" in query
    assert "status in ('done', 'failed')" in query
    assert "delete" not in query.lower()


async def test_sweep_job_logs_returns_zero_when_nothing_is_due():
    conn = FakeConnection(scrub_rows=[])

    assert await sweep_job_logs(conn, now=_NOW) == 0


async def test_run_retention_sweep_records_the_success_metric_on_full_success():
    conn = FakeConnection(bronze_rows=[_bronze_row()], scrub_rows=[{"id": "job-1"}])
    blob = FakeBlob()

    result = await run_retention_sweep(conn, blob=blob, now=_NOW)

    assert result.bronze_purged == 1
    assert result.job_logs_scrubbed == 1
    assert result.swept_at == _NOW
    gauge_value = metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert gauge_value == _NOW.timestamp()


async def test_run_retention_sweep_does_not_record_success_on_failure():
    metrics.record_sweeper_success(when=123.0)

    class ExplodingConnection(FakeConnection):
        async def execute(self, query, *args):
            raise RuntimeError("db exploded")

    conn = ExplodingConnection(bronze_rows=[_bronze_row()])
    blob = FakeBlob()

    with pytest.raises(RuntimeError):
        await run_retention_sweep(conn, blob=blob, now=_NOW)

    gauge_value = metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert gauge_value == 123.0


async def test_run_retention_sweep_does_not_record_success_when_a_blob_delete_fails():
    metrics.record_sweeper_success(when=123.0)

    ok_row = _bronze_row(id="bronze-ok", blob_url="https://blob.example/ok")
    failing_row = _bronze_row(id="bronze-fail", blob_url="https://blob.example/fail")
    conn = FakeConnection(
        bronze_rows=[failing_row, ok_row], scrub_rows=[{"id": "job-1"}]
    )
    blob = FakeBlob(fail_urls=frozenset([failing_row["blob_url"]]))

    result = await run_retention_sweep(conn, blob=blob, now=_NOW)

    # the run still completes and reports what it could purge/scrub...
    assert result.bronze_purged == 1
    assert result.job_logs_scrubbed == 1
    # ...but the failed blob delete must keep the success metric stale, or
    # AA-19's "sweeper-stale is a privacy incident" alert never fires.

    gauge_value = metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert gauge_value == 123.0
