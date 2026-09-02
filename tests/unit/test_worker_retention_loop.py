"""Unit tests for `worker.main.retention_sweep_loop` (KCH-54 / AA-19).

Same fake-connection approach as tests/unit/test_worker_job_poll_loop.py —
proves the loop calls into `worker.retention.run_retention_sweep` each cycle
and stops promptly, without a live Postgres or Blob store.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from worker import metrics
from worker.main import retention_sweep_loop

pytestmark = pytest.mark.asyncio


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeConnection:
    """No rows ever due — proves the loop runs the sweep and stops, not the
    row-level sweep behavior (that's tests/unit/test_worker_retention_sweep.py)."""

    def __init__(self, stop_event: asyncio.Event):
        self._stop_event = stop_event
        self.fetch_calls = 0

    def transaction(self):
        return _FakeTransaction()

    async def fetch(self, query: str, *args):
        self.fetch_calls += 1
        self._stop_event.set()
        return []

    async def execute(self, query: str, *args):  # pragma: no cover - no rows to update
        pass

    async def fetchrow(self, query: str, *args):  # pragma: no cover - no rows to update
        return {"id": "unused"}


class FakeBlob:
    def put(self, pathname, data, content_type):  # pragma: no cover - unused
        raise NotImplementedError

    def delete(self, url):  # pragma: no cover - no rows to delete
        pass


async def test_retention_sweep_loop_runs_a_sweep_and_stops_promptly():
    stop_event = asyncio.Event()
    conn = FakeConnection(stop_event)

    await asyncio.wait_for(
        retention_sweep_loop(conn, stop_event, blob=FakeBlob()), timeout=2
    )

    assert conn.fetch_calls >= 1


async def test_retention_sweep_loop_records_the_success_metric():
    stop_event = asyncio.Event()
    conn = FakeConnection(stop_event)
    before = datetime.now(UTC).timestamp()

    await asyncio.wait_for(
        retention_sweep_loop(conn, stop_event, blob=FakeBlob()), timeout=2
    )

    gauge_value = metrics.RETENTION_SWEEPER_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert gauge_value >= before


async def test_retention_sweep_loop_survives_a_failing_sweep():
    stop_event = asyncio.Event()

    class ExplodingConnection(FakeConnection):
        async def fetch(self, query, *args):
            self._stop_event.set()
            raise RuntimeError("db exploded")

    conn = ExplodingConnection(stop_event)

    # Must not raise — a failed sweep logs and retries next interval rather
    # than taking down the heartbeat/job-poll loops sharing this process.
    await asyncio.wait_for(
        retention_sweep_loop(conn, stop_event, blob=FakeBlob()), timeout=2
    )
