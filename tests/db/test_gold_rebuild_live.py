"""Live proof of `worker.gold.rebuild_gold`'s DB reads/writes (KCH-53 / AA-18).

Same ephemeral-Postgres approach as tests/db/test_staged_rows_confirm.py:
gold's delete-then-reinsert rebuildability, the vested-lot valuation filter,
and the room-contribution -> `room_events` link are real Postgres behaviour
a mocked connection can't prove. Skips cleanly wherever Postgres tooling is
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


async def _insert_transaction(
    conn, *, user_id, account_id, kind, amount, occurred_at, currency="CAD"
):
    return await conn.fetchval(
        """
        insert into public.transactions
            (user_id, account_id, occurred_at, kind, amount, currency)
        values ($1, $2, $3, $4, $5, $6)
        returning id
        """,
        user_id,
        account_id,
        occurred_at,
        kind,
        amount,
        currency,
    )


async def test_rebuild_gold_computes_networth_buckets_and_cuts(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    chequing_id = await _insert_account(
        conn, user_id=user_id, institution="scotia", account_type="chequing", mask="scotia-...4821"
    )
    await _insert_transaction(
        conn,
        user_id=user_id,
        account_id=chequing_id,
        kind="credit",
        amount=Decimal("3450.00"),
        occurred_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    await _insert_transaction(
        conn,
        user_id=user_id,
        account_id=chequing_id,
        kind="debit",
        amount=Decimal("1000.00"),
        occurred_at=datetime(2026, 7, 3, tzinfo=UTC),
    )

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
        insert into public.liabilities (user_id, kind, balance, currency)
        values ($1, 'line_of_credit', $2, 'CAD')
        """,
        user_id,
        Decimal("9800.00"),
    )

    lineage = LineageEmitter(conn, user_id=user_id)
    result = await rebuild_gold(
        conn, user_id=user_id, snapshot_date=date(2026, 7, 31), lineage=lineage
    )

    assert result.totals.total_assets_cad == Decimal("4250.00")
    assert result.totals.total_liabilities_cad == Decimal("9800.00")
    assert result.totals.net_worth_cad == Decimal("-5550.00")

    snapshot = await conn.fetchrow(
        "select * from public.networth_snapshots where user_id = $1", user_id
    )
    assert snapshot["net_worth_cad"] == Decimal("-5550.00")
    assert snapshot["run_id"] == uuid.UUID(lineage.run_id)

    buckets = {
        row["bucket"]: row["amount_cad"]
        for row in await conn.fetch("select * from public.term_buckets where user_id = $1", user_id)
    }
    assert buckets == {
        "short_term": Decimal("2450.00"),
        "long_term": Decimal("1800.00"),
        "liabilities": Decimal("9800.00"),
    }

    cuts = {
        (row["cut"], row["label"]): row["amount_cad"]
        for row in await conn.fetch(
            "select * from public.diversification_cuts where user_id = $1", user_id
        )
    }
    assert cuts[("institution", "scotia")] == Decimal("2450.00")
    assert cuts[("institution", "questrade")] == Decimal("1800.00")
    assert cuts[("currency", "USD")] == Decimal("1800.00")


async def test_rebuild_gold_is_idempotent_across_reruns(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    account_id = await _insert_account(
        conn, user_id=user_id, institution="scotia", account_type="savings", mask="scotia-...7710"
    )
    await _insert_transaction(
        conn,
        user_id=user_id,
        account_id=account_id,
        kind="credit",
        amount=Decimal("500.00"),
        occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
    )

    snapshot_date = date(2026, 7, 31)
    await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=snapshot_date,
        lineage=LineageEmitter(conn, user_id=user_id),
    )
    # A second contribution arrives, then the gold rebuild re-runs for the
    # same day — the earlier snapshot's rows must be replaced, not duplicated.
    await _insert_transaction(
        conn,
        user_id=user_id,
        account_id=account_id,
        kind="credit",
        amount=Decimal("50.00"),
        occurred_at=datetime(2026, 7, 31, tzinfo=UTC),
    )
    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=snapshot_date,
        lineage=LineageEmitter(conn, user_id=user_id),
    )

    assert result.totals.total_assets_cad == Decimal("550.00")
    snapshots = await conn.fetch(
        "select * from public.networth_snapshots where user_id = $1", user_id
    )
    assert len(snapshots) == 1
    assert snapshots[0]["net_worth_cad"] == Decimal("550.00")
    buckets = await conn.fetch("select * from public.term_buckets where user_id = $1", user_id)
    assert len(buckets) == 1
    assert buckets[0]["amount_cad"] == Decimal("550.00")


async def test_rebuild_gold_derives_and_replaces_room_contribution_events(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    fhsa_id = await _insert_account(
        conn,
        user_id=user_id,
        institution="wealthsimple",
        account_type="fhsa_invest",
        mask="ws-...1187",
    )
    txn_id = await _insert_transaction(
        conn,
        user_id=user_id,
        account_id=fhsa_id,
        kind="contribution",
        amount=Decimal("8000.00"),
        occurred_at=datetime(2024, 12, 31, tzinfo=UTC),
    )

    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=date(2026, 7, 31),
        lineage=LineageEmitter(conn, user_id=user_id),
    )
    assert result.room_events_written == 1

    events = await conn.fetch(
        "select * from public.room_events where user_id = $1 and kind = 'contribution'", user_id
    )
    assert len(events) == 1
    assert events[0]["account_type"] == "fhsa"
    assert events[0]["year"] == 2024
    assert events[0]["amount"] == Decimal("8000.00")
    assert events[0]["source_ref"] == txn_id

    # Rebuilding again with no new contributions must not duplicate the event.
    result_again = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=date(2026, 7, 31),
        lineage=LineageEmitter(conn, user_id=user_id),
    )
    assert result_again.room_events_written == 1
    events_again = await conn.fetch(
        "select * from public.room_events where user_id = $1 and kind = 'contribution'", user_id
    )
    assert len(events_again) == 1


async def test_rebuild_gold_excludes_unvested_esop_lots_from_valuation(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    esop_id = await _insert_account(
        conn,
        user_id=user_id,
        institution="equateaccess",
        account_type="esop",
        mask="equateaccess-plan-a",
    )
    holding_id = await conn.fetchval(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency)
        values ($1, $2, 'plan-a', $3, $4, 'CAD')
        returning id
        """,
        user_id,
        esop_id,
        Decimal("75"),
        Decimal("38"),
    )
    await conn.execute(
        """
        insert into public.lots (user_id, holding_id, quantity, unit_cost, currency, vested)
        values ($1, $2, 45, 38, 'CAD', true), ($1, $2, 30, 38, 'CAD', false)
        """,
        user_id,
        holding_id,
    )

    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=date(2026, 7, 31),
        lineage=LineageEmitter(conn, user_id=user_id),
    )

    assert result.totals.total_assets_cad == Decimal("1710")
