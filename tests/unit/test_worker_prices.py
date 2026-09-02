"""Unit tests for `worker.prices.refresh_prices` (KCH-58 / AA-21).

Same fake-connection approach as `tests/unit/test_worker_retention_sweep.py`
— proves the DB read/write shape and partial-failure handling without a live
Postgres or network access to yfinance.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from worker import metrics
from worker.prices import PriceQuote, refresh_prices

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 9, 2)


class FakeConnection:
    def __init__(self, *, tickers: list[str], currencies: list[str]):
        self._tickers = tickers
        self._currencies = currencies
        self.upserts: list[tuple] = []

    async def fetch(self, query: str, *args):
        if "distinct ticker" in query:
            return [{"ticker": t} for t in self._tickers]
        if "currency" in query:
            return [{"currency": c} for c in self._currencies]
        raise AssertionError(f"unexpected query: {query}")

    async def execute(self, query: str, *args):
        self.upserts.append(args)


class FakeFetcher:
    SOURCE = "fake"

    def __init__(self, quotes: dict[str, PriceQuote]):
        self._quotes = quotes
        self.calls: list[list[str]] = []

    def fetch(self, symbols: list[str]) -> dict[str, PriceQuote]:
        self.calls.append(list(symbols))
        return {symbol: self._quotes[symbol] for symbol in symbols if symbol in self._quotes}


async def test_refresh_prices_writes_a_row_per_ticker_and_fx_symbol():
    conn = FakeConnection(tickers=["AAPL", "VFV.TO"], currencies=["USD"])
    fetcher = FakeFetcher(
        {
            "AAPL": PriceQuote(close=Decimal("220.50"), as_of=TODAY),
            "VFV.TO": PriceQuote(close=Decimal("130.10"), as_of=TODAY),
            "USDCAD=X": PriceQuote(close=Decimal("1.35"), as_of=TODAY),
        }
    )

    result = await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    assert result.prices_written == 2
    assert result.fx_written == 1
    assert result.symbols_failed == []
    assert len(conn.upserts) == 3
    assert ("USDCAD=X", TODAY, Decimal("1.35"), "fake") in conn.upserts


async def test_refresh_prices_uses_the_fx_symbol_not_the_raw_currency_code():
    conn = FakeConnection(tickers=[], currencies=["USD", "BTC"])
    fetcher = FakeFetcher(
        {
            "USDCAD=X": PriceQuote(close=Decimal("1.35"), as_of=TODAY),
            "BTC-CAD": PriceQuote(close=Decimal("90000"), as_of=TODAY),
        }
    )

    await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    assert fetcher.calls[-1] == ["USDCAD=X", "BTC-CAD"]


async def test_refresh_prices_maps_a_crypto_ticker_to_its_cad_pair_once():
    # worker.adapters.kraken stages a crypto holding's asset code as both
    # ticker and currency, so BTC shows up in both lists here — the refresh
    # must fetch/write BTC-CAD exactly once, counted as a price, not an FX
    # rate, rather than also requesting an unpriceable raw "BTC" ticker.
    conn = FakeConnection(tickers=["AAPL", "BTC"], currencies=["USD", "BTC"])
    fetcher = FakeFetcher(
        {
            "AAPL": PriceQuote(close=Decimal("220.50"), as_of=TODAY),
            "BTC-CAD": PriceQuote(close=Decimal("90000"), as_of=TODAY),
            "USDCAD=X": PriceQuote(close=Decimal("1.35"), as_of=TODAY),
        }
    )

    result = await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    assert result.prices_written == 2
    assert result.fx_written == 1
    assert fetcher.calls == [["AAPL", "BTC-CAD"], ["USDCAD=X"]]
    assert conn.upserts.count(("BTC-CAD", TODAY, Decimal("90000"), "fake")) == 1


async def test_refresh_prices_skips_symbols_the_fetcher_could_not_price():
    conn = FakeConnection(tickers=["AAPL", "BADTICKER"], currencies=[])
    fetcher = FakeFetcher({"AAPL": PriceQuote(close=Decimal("220.50"), as_of=TODAY)})

    result = await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    assert result.prices_written == 1
    assert result.symbols_failed == ["BADTICKER"]
    assert len(conn.upserts) == 1


async def test_refresh_prices_does_not_call_the_fetcher_when_nothing_is_held():
    conn = FakeConnection(tickers=[], currencies=[])
    fetcher = FakeFetcher({})

    result = await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    assert result.prices_written == 0
    assert result.fx_written == 0
    assert fetcher.calls == []


async def test_refresh_prices_records_success_metric_only_on_full_success():
    conn = FakeConnection(tickers=["AAPL"], currencies=[])
    fetcher = FakeFetcher({"AAPL": PriceQuote(close=Decimal("220.50"), as_of=TODAY)})
    when = datetime.now(UTC)

    await refresh_prices(conn, fetcher=fetcher, now=when)

    gauge_value = metrics.PRICE_REFRESH_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert gauge_value == pytest.approx(when.timestamp())


async def test_refresh_prices_does_not_record_success_metric_on_partial_failure():
    conn = FakeConnection(tickers=["AAPL", "BADTICKER"], currencies=[])
    fetcher = FakeFetcher({"AAPL": PriceQuote(close=Decimal("220.50"), as_of=TODAY)})
    before = metrics.PRICE_REFRESH_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value

    await refresh_prices(conn, fetcher=fetcher, now=datetime.now(UTC))

    after = metrics.PRICE_REFRESH_LAST_SUCCESS_TIMESTAMP.collect()[0].samples[0].value
    assert after == before
