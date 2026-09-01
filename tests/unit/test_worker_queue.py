"""Unit tests for `worker.queue` (KCH-46 / AA-11).

Same fake-connection approach as tests/unit/test_worker_heartbeat.py — proves
the query shape (parameterized, correct bind order) without a live Postgres.
The actual `FOR UPDATE SKIP LOCKED` concurrency guarantee can only be proven
against real Postgres; that's tests/db/test_etl_jobs_queue.py, which skips
here per CLAUDE.md (no local Postgres in this sandbox).
"""

from __future__ import annotations

import pytest

from worker.queue import claim_next_job, count_queued_jobs, release_job

pytestmark = pytest.mark.asyncio


class FakeConnection:
    def __init__(self, fetchrow_result=None, fetchval_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchrow_result = fetchrow_result
        self._fetchval_result = fetchval_result

    async def fetchrow(self, query: str, *args):
        self.calls.append((query, args))
        return self._fetchrow_result

    async def fetchval(self, query: str, *args):
        self.calls.append((query, args))
        return self._fetchval_result


async def test_claim_next_job_binds_claimed_by_as_a_parameter():
    conn = FakeConnection(fetchrow_result={"id": "job-1"})

    result = await claim_next_job(conn, claimed_by="worker-a")

    assert result == {"id": "job-1"}
    query, args = conn.calls[0]
    assert "for update skip locked" in query
    assert "$1" in query
    assert "worker-a" not in query  # CLAUDE.md hard rule #3: never interpolated
    assert args == ("worker-a",)


async def test_claim_next_job_returns_none_when_queue_is_empty():
    conn = FakeConnection(fetchrow_result=None)

    result = await claim_next_job(conn, claimed_by="worker-a")

    assert result is None


async def test_release_job_binds_owner_expected_status_status_and_error():
    conn = FakeConnection(fetchrow_result={"id": "job-1", "status": "done"})

    await release_job(
        conn, job_id="job-1", claimed_by="worker-a", expected_status="parsing", status="done"
    )

    query, args = conn.calls[0]
    assert "claimed_by = $2" in query
    assert "status = $3" in query
    assert args == ("job-1", "worker-a", "parsing", "done", None)


async def test_release_job_rejects_an_unknown_status():
    conn = FakeConnection()

    with pytest.raises(ValueError, match="invalid etl_jobs status"):
        await release_job(
            conn, job_id="job-1", claimed_by="worker-a", expected_status="parsing", status="bogus"
        )

    assert conn.calls == []


async def test_release_job_rejects_an_unknown_expected_status():
    conn = FakeConnection()

    with pytest.raises(ValueError, match="invalid etl_jobs status"):
        await release_job(
            conn, job_id="job-1", claimed_by="worker-a", expected_status="bogus", status="done"
        )

    assert conn.calls == []


async def test_release_job_rejects_a_disallowed_transition():
    conn = FakeConnection()

    with pytest.raises(ValueError, match="invalid etl_jobs transition"):
        await release_job(
            conn, job_id="job-1", claimed_by="worker-a", expected_status="done", status="claimed"
        )

    assert conn.calls == []


async def test_count_queued_jobs_returns_the_pending_count():
    conn = FakeConnection(fetchval_result=3)

    result = await count_queued_jobs(conn)

    assert result == 3
    query, args = conn.calls[0]
    assert "status = 'pending'" in query
    assert args == ()
