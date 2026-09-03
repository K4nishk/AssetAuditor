"""Live proof that `worker.gold.rebuild_gold` reconciles non-CAD assets to
CAD using AA-21's price layer before writing the gold tables (KCH-59 / AA-22).

Same ephemeral-Postgres approach as tests/db/test_gold_rebuild_live.py (AA-18
owns that file; this one stays separate rather than editing a settled
upstream contract). `app.domain.gold`'s own docstring flags face-value-only
totals as "approximate ... until AA-21 lands; that reconciliation is AA-22's
dashboard concern" — this proves the reconciliation is real against a live
DB, not just the pure unit tests in tests/unit/test_dashboard_domain.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from worker.gold import rebuild_gold
from worker.lineage import LineageEmitter

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


async def test_rebuild_gold_converts_usd_holding_to_cad_using_prices_fx_row(seeded_db):
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
    # AA-21's price layer: FX rate stored under the currency's canonical
    # yfinance-style ticker (app.domain.prices.fx_symbol_for_currency).
    await conn.execute(
        """
        insert into public.prices (ticker, date, close, source)
        values ('USDCAD=X', $1, $2, 'yfinance')
        """,
        date(2026, 7, 31),
        Decimal("1.35"),
    )

    lineage = LineageEmitter(conn, user_id=user_id)
    result = await rebuild_gold(
        conn, user_id=user_id, snapshot_date=date(2026, 7, 31), lineage=lineage
    )

    # face value 10 * 180.00 USD = 1800.00, converted at 1.35 -> 2430.00 CAD
    assert result.totals.total_assets_cad == Decimal("2430.0000")
    assert result.totals.diversification_cuts[("currency", "USD")] == Decimal("2430.0000")

    snapshot = await conn.fetchrow(
        "select * from public.networth_snapshots where user_id = $1", user_id
    )
    assert snapshot["total_assets_cad"] == Decimal("2430.0000")


async def test_rebuild_gold_fails_lineage_when_no_fx_rate_available(seeded_db):
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

    lineage = LineageEmitter(conn, user_id=user_id)
    with pytest.raises(Exception, match="no FX rate available"):
        await rebuild_gold(
            conn, user_id=user_id, snapshot_date=date(2026, 7, 31), lineage=lineage
        )

    fail_event = await conn.fetchrow(
        """
        select payload from public.lineage_events
        where user_id = $1 and step = 'gold_rebuild' and event_type = 'FAIL'
        """,
        user_id,
    )
    assert fail_event is not None


async def test_rebuild_gold_leaves_cad_only_portfolio_unaffected(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

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

    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=date(2026, 7, 31),
        lineage=LineageEmitter(conn, user_id=user_id),
    )

    assert result.totals.total_assets_cad == Decimal("4200.00")
