"""Unit tests for `app.db.queries.worker_heartbeat` (KCH-57 / AA-34).

Same fake-connection approach as tests/unit/test_worker_queue.py — proves the
query shape without a live Postgres.
"""

from __future__ import annotations

import pytest

from app.db.queries.worker_heartbeat import get_latest_heartbeat

pytestmark = pytest.mark.asyncio


class FakeConnection:
    def __init__(self, fetchrow_result=None):
        self.calls: list[tuple[str, tuple]] = []
        self._fetchrow_result = fetchrow_result

    async def fetchrow(self, query: str, *args):
        self.calls.append((query, args))
        return self._fetchrow_result


async def test_get_latest_heartbeat_queries_the_single_row():
    conn = FakeConnection(fetchrow_result={"id": 1, "last_beat_at": "t", "status": "online"})

    result = await get_latest_heartbeat(conn)

    assert result == {"id": 1, "last_beat_at": "t", "status": "online"}
    query, args = conn.calls[0]
    assert "worker_heartbeat" in query
    assert "id = 1" in query
    assert args == ()


async def test_get_latest_heartbeat_returns_none_when_no_row_exists():
    conn = FakeConnection(fetchrow_result=None)

    result = await get_latest_heartbeat(conn)

    assert result is None
