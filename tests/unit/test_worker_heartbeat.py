"""Unit tests for the worker heartbeat loop (KCH-39 / AA-4).

No database required — exercises `send_heartbeat`/`heartbeat_loop` against a
fake asyncpg-shaped connection so this always runs in CI. The live-DB proof
(a real row lands in `worker_heartbeat`) is tests/db/test_worker_heartbeat.py.
"""

import asyncio

import pytest

from worker.main import heartbeat_loop, send_heartbeat

pytestmark = pytest.mark.asyncio


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args):
        self.calls.append((query, args))


async def test_send_heartbeat_uses_a_parameterized_query():
    conn = FakeConnection()

    await send_heartbeat(conn, status="online")

    assert len(conn.calls) == 1
    query, args = conn.calls[0]
    # CLAUDE.md hard rule #3: parameterized only — the status value must
    # travel as a bind parameter, never interpolated into the SQL text.
    assert "$1" in query
    assert "online" not in query
    assert args == ("online",)


async def test_send_heartbeat_defaults_to_online_status():
    conn = FakeConnection()

    await send_heartbeat(conn)

    assert conn.calls[0][1] == ("online",)


async def test_heartbeat_loop_stops_promptly_once_the_stop_event_is_set():
    conn = FakeConnection()
    stop_event = asyncio.Event()

    async def execute_and_stop(query, *args):
        conn.calls.append((query, args))
        stop_event.set()

    conn.execute = execute_and_stop  # type: ignore[method-assign]

    await asyncio.wait_for(heartbeat_loop(conn, stop_event), timeout=2)

    assert len(conn.calls) == 1
