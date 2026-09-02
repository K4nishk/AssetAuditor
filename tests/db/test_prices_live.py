"""Live proof of `worker.prices.refresh_prices` + `public.prices` RLS (KCH-58 / AA-21).

Same ephemeral-Postgres approach as tests/db/test_retention_sweep_live.py:
the `(ticker, date, source)` unique constraint (idempotent upsert) and the
`authenticated` role's read-only grant on `prices` are Postgres guarantees a
fake connection can't prove. Skips cleanly wherever Postgres tooling is
unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.prices import latest_price_on_or_before, upsert_price
from worker.prices import PriceQuote, refresh_prices

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")

AUTH_STUB_SQL = """
create schema auth;

create table auth.users (
    id uuid primary key default gen_random_uuid(),
    email text
);

create function auth.uid() returns uuid
language sql stable
as $$
    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;

grant usage on schema auth to authenticated;
grant execute on function auth.uid() to authenticated;
grant usage on schema public to authenticated;
"""

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 9, 2, tzinfo=UTC)
_TODAY = date(2026, 9, 2)


class FakeFetcher:
    SOURCE = "fake"

    def __init__(self, quotes: dict[str, PriceQuote]):
        self._quotes = quotes

    def fetch(self, symbols: list[str]) -> dict[str, PriceQuote]:
        return {symbol: self._quotes[symbol] for symbol in symbols if symbol in self._quotes}


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)

        user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", user_id)

        account_id = uuid.uuid4()
        await admin.execute(
            """
            insert into public.accounts (id, user_id, institution, account_type, currency)
            values ($1, $2, 'questrade', 'tfsa', 'CAD')
            """,
            account_id,
            user_id,
        )

        await admin.execute(
            """
            insert into public.holdings (user_id, account_id, ticker, quantity, currency)
            values ($1, $2, 'AAPL', 10, 'USD'), ($1, $2, 'BTC', 0.05, 'BTC')
            """,
            user_id,
            account_id,
        )

        yield {"conn": admin, "user_id": user_id}
    finally:
        await admin.close()


async def test_refresh_prices_writes_ticker_and_fx_rows_from_live_holdings(seeded_db):
    conn = seeded_db["conn"]
    fetcher = FakeFetcher(
        {
            "AAPL": PriceQuote(close=Decimal("220.50"), as_of=_TODAY),
            "BTC-CAD": PriceQuote(close=Decimal("90000.123456"), as_of=_TODAY),
            "USDCAD=X": PriceQuote(close=Decimal("1.35"), as_of=_TODAY),
        }
    )

    result = await refresh_prices(conn, fetcher=fetcher, now=_NOW)

    assert result.prices_written == 2
    assert result.fx_written == 1

    rows = {
        row["ticker"]: row
        for row in await conn.fetch("select ticker, date, close, source from public.prices")
    }
    assert rows["AAPL"]["close"] == Decimal("220.50")
    assert rows["AAPL"]["source"] == "fake"
    assert rows["BTC-CAD"]["close"] == Decimal("90000.123456")
    assert rows["USDCAD=X"]["date"] == _TODAY


async def test_refresh_prices_upsert_is_idempotent_for_the_same_ticker_date_source(seeded_db):
    conn = seeded_db["conn"]
    first = FakeFetcher({"AAPL": PriceQuote(close=Decimal("220.50"), as_of=_TODAY)})
    await refresh_prices(conn, fetcher=first, now=_NOW)

    second = FakeFetcher({"AAPL": PriceQuote(close=Decimal("221.75"), as_of=_TODAY)})
    await refresh_prices(conn, fetcher=second, now=_NOW)

    rows = await conn.fetch("select close from public.prices where ticker = 'AAPL'")
    assert [row["close"] for row in rows] == [Decimal("221.75")]


async def test_authenticated_role_can_read_but_not_write_prices(
    seeded_db, pg_cluster, scratch_database
):
    admin = seeded_db["conn"]
    await admin.execute(
        "insert into public.prices (ticker, date, close, source) "
        "values ('AAPL', $1, 220.5, 'fake')",
        _TODAY,
    )

    user_conn = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        async with user_conn.transaction():
            await user_conn.execute("set local role authenticated")
            readable = await user_conn.fetch("select ticker from public.prices")
            assert [row["ticker"] for row in readable] == ["AAPL"]

            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await user_conn.execute(
                    "insert into public.prices (ticker, date, close, source) "
                    "values ('VFV.TO', $1, 130.1, 'fake')",
                    _TODAY,
                )
    finally:
        await user_conn.close()


async def test_latest_price_on_or_before_looks_backward_across_missing_dates(seeded_db):
    conn = seeded_db["conn"]
    await upsert_price(
        conn, ticker="AAPL", price_date=date(2026, 8, 28), close=Decimal("219.00"), source="fake"
    )
    await upsert_price(
        conn, ticker="AAPL", price_date=date(2026, 9, 1), close=Decimal("220.50"), source="fake"
    )

    row = await latest_price_on_or_before(conn, ticker="AAPL", as_of=_TODAY)

    assert row["date"] == date(2026, 9, 1)
    assert row["close"] == Decimal("220.50")


async def test_latest_price_on_or_before_returns_none_when_no_row_exists(seeded_db):
    conn = seeded_db["conn"]

    row = await latest_price_on_or_before(conn, ticker="NOPE", as_of=_TODAY)

    assert row is None
