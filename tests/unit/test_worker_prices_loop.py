"""Unit tests for `worker.main.price_refresh_loop` (KCH-58 / AA-21).

Same fake-connection approach as `tests/unit/test_worker_retention_loop.py`
— proves the loop calls into `worker.prices.refresh_prices` each cycle and
stops promptly, without a live Postgres or network access.
"""

from __future__ import annotations

import asyncio

import pytest

from worker.main import price_refresh_loop
from worker.prices import PriceQuote

pytestmark = pytest.mark.asyncio


class FakeConnection:
    """No tickers/currencies ever due — proves the loop runs a refresh and
    stops, not the row-level refresh behavior (that's
    tests/unit/test_worker_prices.py)."""

    def __init__(self, stop_event: asyncio.Event):
        self._stop_event = stop_event
        self.fetch_calls = 0

    async def fetch(self, query: str, *args):
        self.fetch_calls += 1
        self._stop_event.set()
        return []

    async def execute(self, query: str, *args):  # pragma: no cover - no rows to upsert
        pass


class FakeFetcher:
    SOURCE = "fake"

    def fetch(self, symbols: list[str]) -> dict[str, PriceQuote]:  # pragma: no cover - unused
        raise NotImplementedError


async def test_price_refresh_loop_runs_a_refresh_and_stops_promptly():
    stop_event = asyncio.Event()
    conn = FakeConnection(stop_event)

    await asyncio.wait_for(
        price_refresh_loop(conn, stop_event, fetcher=FakeFetcher()), timeout=2
    )

    assert conn.fetch_calls >= 1


async def test_price_refresh_loop_survives_a_failing_refresh():
    stop_event = asyncio.Event()

    class ExplodingConnection(FakeConnection):
        async def fetch(self, query, *args):
            self._stop_event.set()
            raise RuntimeError("db exploded")

    conn = ExplodingConnection(stop_event)

    # Must not raise — a failed refresh logs and retries next interval
    # rather than taking down the other loops sharing this process.
    await asyncio.wait_for(
        price_refresh_loop(conn, stop_event, fetcher=FakeFetcher()), timeout=2
    )
