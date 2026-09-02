"""Price + FX refresh (KCH-58 / AA-21).

`docs/vault/Assumptions.md` A7 / `CLARIFICATIONS.md` Q6: market data comes
from a free-tier source (yfinance), refreshed daily + on demand, end-of-day
only, every rate recorded with its source and date. yfinance is the only
fetcher implemented here — `docs/vault/40-research/OSS-Portfolio-Trackers.md`
flags OpenBB as a candidate market-data layer too, but pulling in a second
dependency for one issue's zero-cost, single-user MVP isn't worth it;
`PriceFetcher` is the seam a future OpenBB-backed implementation would slot
into without touching `refresh_prices` itself.

Same daily-cron/on-demand split `worker/retention.py` already established:
`worker.main`'s `price_refresh_loop` runs this once/24h on the long-lived
worker process (the "daily cron"), and `python -m worker.prices` is the
manual one-off run (the "on-demand refresh") — `app/routes/internal.py`'s
`INTERNAL_HMAC_SECRET`-authenticated route stub stays deferred, same status
AA-19 already left it in, rather than standing up a second trigger path for
data the worker box can already refresh itself on request.

`public.prices` is shared, not user-scoped (migration 0001's comment: "not
user-scoped... writes come from the price-refresh job's service_role only"),
so `refresh_prices` looks at tickers/currencies across *every* user's
holdings/accounts/liabilities/lots — the same cross-tenant read
`worker/retention.py`'s sweeps already do on the worker's service_role
connection, safe here for the same reason: AssetAuditor is a single-user
app (CLAUDE.md header), the RLS scaffolding exists for defense-in-depth, not
because there's a second tenant's data to leak.

No `lineage_events` here: that table's provenance is for per-user silver/gold
writes (CLAUDE.md hard rule #1), and a `prices` row already carries its own
provenance columns (`source`, `date`) — the exact "prices table with
source+date" mvp.md's AA-21 asks for. FX conversion of a user's holdings
into CAD (pairing a `prices` FX row with a face-value amount) is AA-22's
dashboard concern, per `app.domain.gold`'s module docstring.

**Unverified**: no network in this sandbox, so `YFinancePriceFetcher` has
never actually called `yfinance` — `refresh_prices`'s DB behavior is proven
against a fake `PriceFetcher` (`tests/unit/test_worker_prices.py`); the real
yfinance wire shape is unverified, same convention as `app.uploads.blob
.VercelBlobStorage`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import NamedTuple, Protocol

import asyncpg

from app.db.queries import prices as prices_queries
from app.domain.prices import fx_symbol_for_currency, price_symbol_for_ticker
from worker import metrics

logger = logging.getLogger("worker.prices")

_TICKERS_NEEDED_SQL = """
    select distinct ticker
    from public.holdings
    where deactivated_at is null
"""

_CURRENCIES_NEEDED_SQL = """
    select currency from public.holdings where deactivated_at is null and currency <> 'CAD'
    union
    select currency from public.accounts where deactivated_at is null and currency <> 'CAD'
    union
    select currency from public.liabilities where deactivated_at is null and currency <> 'CAD'
    union
    select currency from public.lots where deactivated_at is null and currency <> 'CAD'
"""


class PriceQuote(NamedTuple):
    close: Decimal
    as_of: date


class PriceFetcher(Protocol):
    SOURCE: str

    def fetch(self, symbols: list[str]) -> dict[str, PriceQuote]:
        """Best-effort quote lookup — a symbol yfinance can't price (bad
        ticker, market holiday with no session yet) is simply absent from
        the returned mapping, never raised, so one bad symbol doesn't sink
        the whole refresh."""
        ...


class YFinancePriceFetcher:
    """`PriceFetcher` backed by `yfinance`'s end-of-day `Ticker.history`."""

    SOURCE = "yfinance"

    def fetch(self, symbols: list[str]) -> dict[str, PriceQuote]:
        import yfinance as yf

        quotes: dict[str, PriceQuote] = {}
        for symbol in symbols:
            try:
                history = yf.Ticker(symbol).history(period="1d")
            except Exception:
                logger.exception("price refresh: failed to fetch %s", symbol)
                continue
            if history.empty:
                logger.warning("price refresh: no session data for %s", symbol)
                continue
            close = Decimal(str(history["Close"].iloc[-1]))
            as_of = history.index[-1].date()
            quotes[symbol] = PriceQuote(close=close, as_of=as_of)
        return quotes


async def tickers_needed(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(_TICKERS_NEEDED_SQL)
    return [row["ticker"] for row in rows]


async def currencies_needed(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(_CURRENCIES_NEEDED_SQL)
    return [row["currency"] for row in rows]


@dataclass(frozen=True)
class PriceRefreshResult:
    prices_written: int
    fx_written: int
    symbols_failed: list[str]
    refreshed_at: datetime


async def refresh_prices(
    conn: asyncpg.Connection, *, fetcher: PriceFetcher, now: datetime | None = None
) -> PriceRefreshResult:
    """Fetch+store today's close for every ticker held and the FX rate for
    every non-CAD currency in use, across all users.

    Only records `price_refresh_last_success_timestamp` when every symbol
    resolved — same "partial failure must not look like success" convention
    `worker.retention.run_retention_sweep` uses for the sweeper's metric, so
    a stuck/misconfigured ticker keeps showing as stale rather than being
    masked by the rest of the refresh succeeding.
    """
    refreshed_at = now if now is not None else datetime.now(UTC)

    tickers = await tickers_needed(conn)
    currencies = await currencies_needed(conn)

    # A crypto holding (`worker.adapters.kraken`) stages its asset code as
    # both `ticker` and `currency`, e.g. `ticker="BTC"`, `currency="BTC"` —
    # map the ticker to its canonical `BTC-CAD` pair here, and drop that same
    # symbol from the FX side below, so the refresh fetches/writes one
    # `BTC-CAD` row instead of a separate, unpriceable raw `BTC` ticker.
    ticker_symbols = {ticker: price_symbol_for_ticker(ticker) for ticker in tickers}
    covered_by_tickers = set(ticker_symbols.values())
    fx_symbols = [
        symbol
        for symbol in (fx_symbol_for_currency(currency) for currency in currencies)
        if symbol not in covered_by_tickers
    ]

    quotes = fetcher.fetch(list(ticker_symbols.values())) if ticker_symbols else {}
    fx_quotes = fetcher.fetch(fx_symbols) if fx_symbols else {}

    prices_written = 0
    for symbol in ticker_symbols.values():
        quote = quotes.get(symbol)
        if quote is None:
            continue
        await prices_queries.upsert_price(
            conn, ticker=symbol, price_date=quote.as_of, close=quote.close, source=fetcher.SOURCE
        )
        prices_written += 1

    fx_written = 0
    for symbol in fx_symbols:
        quote = fx_quotes.get(symbol)
        if quote is None:
            continue
        await prices_queries.upsert_price(
            conn, ticker=symbol, price_date=quote.as_of, close=quote.close, source=fetcher.SOURCE
        )
        fx_written += 1

    symbols_failed = [symbol for symbol in ticker_symbols.values() if symbol not in quotes] + [
        symbol for symbol in fx_symbols if symbol not in fx_quotes
    ]
    if symbols_failed:
        logger.warning("price refresh: no quote for %s", symbols_failed)
    else:
        metrics.record_price_refresh_success(when=refreshed_at.timestamp())

    return PriceRefreshResult(
        prices_written=prices_written,
        fx_written=fx_written,
        symbols_failed=symbols_failed,
        refreshed_at=refreshed_at,
    )


async def main() -> None:
    """Standalone entrypoint (`python -m worker.prices`) for a one-off manual
    run — the "on-demand refresh" mvp.md's AA-21 asks for. The primary
    schedule is `worker.main`'s `price_refresh_loop`, same split
    `worker/retention.py`'s `main()` documents for the retention sweep."""
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["WORKER_DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        result = await refresh_prices(conn, fetcher=YFinancePriceFetcher())
        logger.info(
            "price refresh complete: prices_written=%s fx_written=%s symbols_failed=%s",
            result.prices_written,
            result.fx_written,
            result.symbols_failed,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
