"""Unit tests for `worker.main.job_poll_loop`'s metrics wiring (KCH-57 / AA-34).

Same fake-connection approach as tests/unit/test_worker_heartbeat.py — no
live Postgres, just proof that each poll cycle refreshes the
`etl_jobs_queued` gauge via `worker.queue.count_queued_jobs`.
"""

from __future__ import annotations

import asyncio

import pytest

from worker import metrics
from worker.main import job_poll_loop

pytestmark = pytest.mark.asyncio


class FakeConnection:
    def __init__(self, fetchrow_results, queued_count):
        self._fetchrow_results = list(fetchrow_results)
        self._queued_count = queued_count
        self.fetchval_calls = 0

    async def fetchrow(self, query: str, *args):
        return self._fetchrow_results.pop(0)

    async def fetchval(self, query: str, *args):
        self.fetchval_calls += 1
        return self._queued_count


def _gauge_value(gauge) -> float:
    return gauge.collect()[0].samples[0].value


async def test_job_poll_loop_refreshes_the_queued_gauge_when_the_queue_is_empty():
    conn = FakeConnection(fetchrow_results=[None], queued_count=0)
    stop_event = asyncio.Event()

    async def _fetchrow_and_stop(query, *args):
        stop_event.set()
        return None

    conn.fetchrow = _fetchrow_and_stop  # type: ignore[method-assign]

    await asyncio.wait_for(job_poll_loop(conn, stop_event, worker_id="w-1"), timeout=2)

    assert conn.fetchval_calls == 1
    assert _gauge_value(metrics.ETL_JOBS_QUEUED) == 0


async def test_job_poll_loop_refreshes_the_queued_gauge_after_claiming_a_job():
    stop_event = asyncio.Event()
    claims = [{"id": "job-1", "bronze_file_id": "bronze-1"}, None]

    async def _fetchrow(query, *args):
        result = claims.pop(0)
        if not claims:
            stop_event.set()
        return result

    conn = FakeConnection(fetchrow_results=[], queued_count=5)
    conn.fetchrow = _fetchrow  # type: ignore[method-assign]

    await asyncio.wait_for(job_poll_loop(conn, stop_event, worker_id="w-1"), timeout=2)

    assert conn.fetchval_calls >= 1
    assert _gauge_value(metrics.ETL_JOBS_QUEUED) == 5
