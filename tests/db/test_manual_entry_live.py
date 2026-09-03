"""Live proof of the manual-entry no-PDF path (KCH-55 / AA-20).

Same ephemeral-Postgres approach as tests/db/test_staged_rows_confirm.py:
`app.db.queries.etl_jobs.insert_needs_user_job`'s direct `needs_user` landing
and the natural-key silver resolution for manually-built drafts are real
Postgres/constraint behaviour a mocked connection can't prove. Skips cleanly
wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import staged_rows
from app.db.queries.silver import write_confirmed_rows
from app.domain.manual_entry import (
    AccountBalanceInput,
    AccountInput,
    LotInput,
    PortfolioEntryInput,
    build_account_balance_drafts,
    build_portfolio_drafts,
)

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


async def _insert_bronze(seeded_db) -> str:
    bronze_id = uuid.uuid4()
    await seeded_db["conn"].execute(
        """
        insert into public.bronze_files (id, user_id, sha256, blob_url)
        values ($1, $2, $3, 'https://blob.example/manual-entry')
        """,
        bronze_id,
        seeded_db["user_id"],
        uuid.uuid4().hex + uuid.uuid4().hex,
    )
    return str(bronze_id)


async def test_insert_needs_user_job_lands_directly_at_needs_user(seeded_db):
    from app.db.queries.etl_jobs import insert_needs_user_job

    bronze_id = await _insert_bronze(seeded_db)

    job = await insert_needs_user_job(
        seeded_db["conn"], user_id=seeded_db["user_id"], bronze_file_id=bronze_id
    )

    assert job["status"] == "needs_user"
    assert job["bronze_file_id"] == uuid.UUID(bronze_id)


async def test_portfolio_entry_stages_and_confirms_into_silver(seeded_db):
    from app.db.queries.etl_jobs import insert_needs_user_job

    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    bronze_id = await _insert_bronze(seeded_db)
    job = await insert_needs_user_job(conn, user_id=user_id, bronze_file_id=bronze_id)
    job_id = str(job["id"])

    entry = PortfolioEntryInput(
        account=AccountInput(institution="Questrade", account_type="TFSA", account_number="1234"),
        ticker="AAPL",
        quantity=Decimal("10"),
        lots=[
            LotInput(quantity=Decimal("4"), unit_cost=Decimal("171.20"), acquired_at="2024-03-14"),
            LotInput(quantity=Decimal("6"), unit_cost=Decimal("186.10"), acquired_at="2025-01-10"),
        ],
    )
    drafts = build_portfolio_drafts(entry)

    staged = []
    for draft in drafts:
        staged.append(
            await staged_rows.insert_draft(
                conn,
                user_id=user_id,
                job_id=job_id,
                entity=draft.entity,
                payload=draft.payload,
                confidence=draft.confidence,
                method=draft.method,
            )
        )
    assert all(row["method"] == "manual_entry" for row in staged)

    summary = await write_confirmed_rows(conn, user_id=user_id, rows=staged)

    assert summary == {"account": 1, "holding": 1, "lot": 2, "transaction": 0, "liability": 0}
    holding = await conn.fetchrow("select * from public.holdings where user_id = $1", user_id)
    assert holding["quantity"] == Decimal("10.00000000")
    lots = await conn.fetch("select * from public.lots where user_id = $1", user_id)
    assert len(lots) == 2


async def test_account_balance_entry_confirms_into_a_credit_transaction(seeded_db):
    from app.db.queries.etl_jobs import insert_needs_user_job

    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    bronze_id = await _insert_bronze(seeded_db)
    job = await insert_needs_user_job(conn, user_id=user_id, bronze_file_id=bronze_id)
    job_id = str(job["id"])

    entry = AccountBalanceInput(
        account=AccountInput(
            institution="Scotiabank", account_type="savings", account_number="4821"
        ),
        balance=Decimal("8500.00"),
    )
    drafts = build_account_balance_drafts(entry, occurred_at=datetime(2026, 7, 31, tzinfo=UTC))

    staged = [
        await staged_rows.insert_draft(
            conn,
            user_id=user_id,
            job_id=job_id,
            entity=draft.entity,
            payload=draft.payload,
            confidence=draft.confidence,
            method=draft.method,
        )
        for draft in drafts
    ]

    summary = await write_confirmed_rows(conn, user_id=user_id, rows=staged)

    assert summary == {"account": 1, "holding": 0, "lot": 0, "transaction": 1, "liability": 0}
    txn = await conn.fetchrow("select * from public.transactions where user_id = $1", user_id)
    assert txn["kind"] == "credit"
    assert txn["amount"] == Decimal("8500.00000000")
