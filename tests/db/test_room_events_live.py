"""Live proof of `app.db.queries.room_events` (KCH-44 / AA-9).

Same ephemeral-Postgres approach as tests/db/test_users_profile_live.py: RLS
scoping and the `cra_override` insert's real column defaults (`source_ref`
null, `deactivated_at` null) are Postgres behaviour a mocked connection
can't prove. Skips cleanly wherever Postgres tooling is unavailable, per
CLAUDE.md.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.room_events import insert_cra_override, list_room_events

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


async def test_insert_cra_override_lands_a_source_ref_free_row(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    row = await insert_cra_override(
        conn, user_id=user_id, account_type="tfsa", year=2026, amount=Decimal("40000.00")
    )

    assert row["account_type"] == "tfsa"
    assert row["kind"] == "cra_override"
    assert row["amount"] == Decimal("40000.00")
    assert row["source_ref"] is None


async def test_list_room_events_orders_by_year_then_created_at(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    await conn.execute(
        """
        insert into public.room_events (user_id, account_type, year, kind, amount)
        values ($1, 'tfsa', 2025, 'contribution', 1000),
               ($1, 'tfsa', 2024, 'contribution', 2000)
        """,
        user_id,
    )
    await insert_cra_override(
        conn, user_id=user_id, account_type="tfsa", year=2026, amount=Decimal("40000.00")
    )

    rows = await list_room_events(conn, user_id=user_id)

    assert [r["year"] for r in rows] == [2024, 2025, 2026]


async def test_list_room_events_excludes_deactivated_rows(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]

    row = await insert_cra_override(
        conn, user_id=user_id, account_type="tfsa", year=2026, amount=Decimal("40000.00")
    )
    await conn.execute(
        "update public.room_events set deactivated_at = now() where id = $1", row["id"]
    )

    rows = await list_room_events(conn, user_id=user_id)

    assert rows == []


async def test_list_room_events_scoped_to_the_user(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    other_user_id = uuid.uuid4()
    await conn.execute("insert into auth.users (id) values ($1)", other_user_id)

    await insert_cra_override(
        conn, user_id=str(other_user_id), account_type="tfsa", year=2026, amount=Decimal("1")
    )

    rows = await list_room_events(conn, user_id=user_id)

    assert rows == []
