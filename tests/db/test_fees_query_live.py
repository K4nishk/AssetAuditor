"""Live proof of `app.db.queries.fees.fetch_mer_by_ticker` and that
`app.db.queries.silver.write_confirmed_rows` actually persists a staged
holding's `mer_pct` instead of dropping it (KCH-63 / AA-26).

Same ephemeral-Postgres approach as tests/db/test_diversification_flags_live.py.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import staged_rows
from app.db.queries.fees import fetch_mer_by_ticker
from app.db.queries.silver import write_confirmed_rows

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")
MIGRATION_0003_SQL = Path("app/db/migrations/0003_holdings_fee_drag.sql").read_text()

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
        await admin.execute(MIGRATION_0003_SQL)
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


async def test_fetch_mer_by_ticker_only_returns_holdings_with_a_disclosed_mer(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    td_account_id = await _insert_account(
        conn, user_id=user_id, institution="td", account_type="mutual_fund", mask="td-...6612"
    )
    await conn.execute(
        """
        insert into public.holdings
            (user_id, account_id, ticker, quantity, avg_cost, currency, mer_pct)
        values ($1, $2, 'TD-BALANCED-GROWTH', $3, $4, 'CAD', $5)
        """,
        user_id,
        td_account_id,
        Decimal("500"),
        Decimal("14.40"),
        Decimal("2.18"),
    )

    qt_account_id = await _insert_account(
        conn, user_id=user_id, institution="questrade", account_type="tfsa", mask="qt-...9033"
    )
    await conn.execute(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity, avg_cost, currency)
        values ($1, $2, 'AAPL', $3, $4, 'USD')
        """,
        user_id,
        qt_account_id,
        Decimal("10"),
        Decimal("180.00"),
    )

    mer_by_ticker = await fetch_mer_by_ticker(conn, user_id=user_id)

    assert mer_by_ticker == {"TD-BALANCED-GROWTH": Decimal("2.18")}


async def test_fetch_mer_by_ticker_drops_a_ticker_whose_accounts_disclose_different_mers(
    seeded_db,
):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    td_account_id = await _insert_account(
        conn, user_id=user_id, institution="td", account_type="mutual_fund", mask="td-...6612"
    )
    await conn.execute(
        """
        insert into public.holdings
            (user_id, account_id, ticker, quantity, avg_cost, currency, mer_pct)
        values ($1, $2, 'TD-BALANCED-GROWTH', $3, $4, 'CAD', $5)
        """,
        user_id,
        td_account_id,
        Decimal("500"),
        Decimal("14.40"),
        Decimal("2.18"),
    )

    other_account_id = await _insert_account(
        conn, user_id=user_id, institution="questrade", account_type="rrsp", mask="qt-...1120"
    )
    await conn.execute(
        """
        insert into public.holdings
            (user_id, account_id, ticker, quantity, avg_cost, currency, mer_pct)
        values ($1, $2, 'TD-BALANCED-GROWTH', $3, $4, 'CAD', $5)
        """,
        user_id,
        other_account_id,
        Decimal("100"),
        Decimal("14.40"),
        Decimal("1.99"),
    )

    mer_by_ticker = await fetch_mer_by_ticker(conn, user_id=user_id)

    assert mer_by_ticker == {}


async def test_write_confirmed_rows_persists_mer_pct_from_the_staged_payload(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    bronze_id = uuid.uuid4()
    await conn.execute(
        """
        insert into public.bronze_files (id, user_id, sha256, blob_url)
        values ($1, $2, $3, 'https://blob.example/x')
        """,
        bronze_id,
        user_id,
        uuid.uuid4().hex + uuid.uuid4().hex,
    )
    job_id = uuid.uuid4()
    await conn.execute(
        """
        insert into public.etl_jobs (id, user_id, bronze_file_id, status)
        values ($1, $2, $3, 'needs_user')
        """,
        job_id,
        user_id,
        bronze_id,
    )

    account_row = await staged_rows.insert_draft(
        conn,
        user_id=user_id,
        job_id=str(job_id),
        entity="account",
        payload={
            "institution": "td",
            "account_type": "mutual_fund",
            "masked_identifier": "td-...6612",
            "currency": "CAD",
        },
        confidence=1.0,
        method="deterministic",
    )
    holding_row = await staged_rows.insert_draft(
        conn,
        user_id=user_id,
        job_id=str(job_id),
        entity="holding",
        payload={
            "account_mask": "td-...6612",
            "ticker": "TD-BALANCED-GROWTH",
            "quantity": "500",
            "avg_cost": "14.40",
            "currency": "CAD",
            "mer_pct": "2.18",
        },
        confidence=1.0,
        method="deterministic",
    )

    summary = await write_confirmed_rows(conn, user_id=user_id, rows=[account_row, holding_row])

    assert summary["holding"] == 1
    holding = await conn.fetchrow("select mer_pct from public.holdings where user_id = $1", user_id)
    assert holding["mer_pct"] == Decimal("2.18")

    mer_by_ticker = await fetch_mer_by_ticker(conn, user_id=user_id)
    assert mer_by_ticker == {"TD-BALANCED-GROWTH": Decimal("2.18")}
