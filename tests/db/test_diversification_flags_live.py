"""Live proof that `app.db.queries.diversification.fetch_portfolio_holdings`
reads real holdings/cash balances, carries `ticker`, and CAD-reconciles via
AA-21's price layer (KCH-61 / AA-24).

Same ephemeral-Postgres approach as tests/db/test_gold_rebuild_fx_live.py
(AA-22 owns that file; this one stays separate rather than editing a settled
upstream contract).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.diversification import fetch_portfolio_holdings
from app.domain.diversification_flags import compute_diversification_flags
from app.domain.prices import MissingFxRateError

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")

AUTH_STUB_SQL = """
create schema auth;

create table auth.users (
    id uuid primary key default gen_random_uuid(),
    email text
);

grant usage on schema auth to authenticated;
grant usage on schema public to authenticated;
"""

pytestmark = pytest.mark.asyncio


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
        yield {"conn": admin, "user_id": str(user_id)}
    finally:
        await admin.close()


async def _insert_account(conn, *, user_id, institution, account_type, mask, currency="CAD"):
    return await conn.fetchval(
        """
        insert into public.accounts
            (user_id, institution, account_type, masked_identifier, currency)
        values ($1, $2, $3, $4, $5)
        returning id
        """,
        user_id,
        institution,
        account_type,
        mask,
        currency,
    )


async def test_fetch_portfolio_holdings_carries_ticker_and_cad_reconciles(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    tfsa_id = await _insert_account(
        conn, user_id=user_id, institution="questrade", account_type="tfsa", mask="qt-...9033"
    )
    await conn.execute(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency)
        values ($1, $2, 'AAPL', $3, $4, 'USD')
        """,
        user_id,
        tfsa_id,
        Decimal("10"),
        Decimal("180.00"),
    )
    await conn.execute(
        """
        insert into public.prices (ticker, date, close, source)
        values ('USDCAD=X', $1, $2, 'yfinance')
        """,
        date(2026, 7, 31),
        Decimal("1.35"),
    )

    kraken_id = await _insert_account(
        conn,
        user_id=user_id,
        institution="kraken",
        account_type="crypto_exchange",
        mask="kraken-default",
    )
    await conn.execute(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency)
        values ($1, $2, 'BTC', $3, $4, 'BTC')
        """,
        user_id,
        kraken_id,
        Decimal("0.085"),
        Decimal("1"),
    )
    await conn.execute(
        """
        insert into public.prices (ticker, date, close, source)
        values ('BTC-CAD', $1, $2, 'yfinance')
        """,
        date(2026, 7, 31),
        Decimal("100000"),
    )

    chequing_id = await _insert_account(
        conn, user_id=user_id, institution="scotia", account_type="chequing", mask="scotia-...4821"
    )
    await conn.execute(
        """
        insert into public.transactions (user_id, account_id, occurred_at, kind, amount, currency)
        values ($1, $2, $3, 'credit', $4, 'CAD')
        """,
        user_id,
        chequing_id,
        datetime(2026, 7, 2, tzinfo=UTC),
        Decimal("4200.00"),
    )

    holdings = await fetch_portfolio_holdings(conn, user_id=user_id, as_of=date(2026, 7, 31))

    by_ticker = {h.ticker: h for h in holdings}
    assert by_ticker["AAPL"].amount_cad == Decimal("2430.0000")
    assert by_ticker["AAPL"].institution == "questrade"
    assert by_ticker["BTC"].amount_cad == Decimal("8500.00000")
    assert by_ticker["BTC"].institution == "kraken"

    cash = next(h for h in holdings if h.ticker is None)
    assert cash.amount_cad == Decimal("4200.00")
    assert cash.institution == "scotia"

    # Feeds straight into the flags engine — crypto is a real, nonzero share.
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")
    assert crypto.weight_pct > Decimal("0")


async def test_fetch_portfolio_holdings_raises_on_missing_fx_rate(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    tfsa_id = await _insert_account(
        conn, user_id=user_id, institution="questrade", account_type="tfsa", mask="qt-...9033"
    )
    await conn.execute(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency)
        values ($1, $2, 'AAPL', $3, $4, 'USD')
        """,
        user_id,
        tfsa_id,
        Decimal("10"),
        Decimal("180.00"),
    )
    # No matching public.prices FX row seeded.

    with pytest.raises(MissingFxRateError):
        await fetch_portfolio_holdings(conn, user_id=user_id, as_of=date(2026, 7, 31))
