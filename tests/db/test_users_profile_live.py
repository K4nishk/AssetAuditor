"""Live proof of the `users_profile` upsert/read behaviour (KCH-42 / AA-7).

Same ephemeral-Postgres approach as tests/db/test_manual_entry_live.py: the
`ON CONFLICT (id) DO UPDATE ... WHERE deactivated_at is null` guard is real
Postgres constraint/conflict behaviour a mocked connection can't prove. Skips
cleanly wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.users_profile import get_profile, upsert_profile

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


async def test_upsert_profile_creates_then_replaces_the_row(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    created = await upsert_profile(
        conn,
        user_id=user_id,
        age=35,
        holdings_country="CA",
        year_in_canada=2009,
        fhsa_opened_year=None,
        risk_profile="medium",
        prior_year_earned_income=None,
    )
    assert created["age"] == 35
    assert created["holdings_country"] == "CA"

    replaced = await upsert_profile(
        conn,
        user_id=user_id,
        age=36,
        holdings_country="US",
        year_in_canada=2009,
        fhsa_opened_year=2024,
        risk_profile="high",
        prior_year_earned_income=Decimal("95000.00"),
    )
    assert replaced["age"] == 36
    assert replaced["holdings_country"] == "US"
    assert replaced["prior_year_earned_income"] == Decimal("95000.00")

    rows = await conn.fetch("select * from public.users_profile where id = $1", user_id)
    assert len(rows) == 1


async def test_get_profile_returns_none_for_a_user_with_no_row(seeded_db):
    row = await get_profile(seeded_db["conn"], user_id=seeded_db["user_id"])
    assert row is None


async def test_upsert_profile_refuses_to_resurrect_a_deactivated_row(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    await upsert_profile(
        conn,
        user_id=user_id,
        age=35,
        holdings_country="CA",
        year_in_canada=2009,
        fhsa_opened_year=None,
        risk_profile="medium",
        prior_year_earned_income=None,
    )
    await conn.execute(
        "update public.users_profile set deactivated_at = now() where id = $1", user_id
    )

    result = await upsert_profile(
        conn,
        user_id=user_id,
        age=40,
        holdings_country="CA",
        year_in_canada=2009,
        fhsa_opened_year=None,
        risk_profile="low",
        prior_year_earned_income=None,
    )

    assert result is None
    row = await conn.fetchrow(
        "select age, deactivated_at from public.users_profile where id = $1", user_id
    )
    assert row["age"] == 35
    assert row["deactivated_at"] is not None


async def test_get_profile_excludes_a_deactivated_row(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    await upsert_profile(
        conn,
        user_id=user_id,
        age=35,
        holdings_country="CA",
        year_in_canada=2009,
        fhsa_opened_year=None,
        risk_profile="medium",
        prior_year_earned_income=None,
    )
    await conn.execute(
        "update public.users_profile set deactivated_at = now() where id = $1", user_id
    )

    row = await get_profile(conn, user_id=user_id)
    assert row is None
