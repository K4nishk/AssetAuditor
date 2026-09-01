"""Live proof that migration 0001's RLS policies actually isolate tenants.

Security-Model.md requires: "RLS on every user table from migration 0001;
tests assert cross-user reads return zero rows even with crafted JWTs." This
exercises that against a real (ephemeral, local) Postgres — not a mock —
because RLS is a Postgres-enforced guarantee, and CLAUDE.md's #1 priority is
data provenance/tenancy, not maintainability of the test.

pgsodium isn't installable on vanilla local Postgres (it's a Supabase-managed
extension, already present on every Supabase project), so that one line is
stripped before applying the migration here; its presence in the migration
file is covered instead by the static test in
tests/unit/test_migration_0001_schema.py. `auth.users`/`auth.uid()` and the
`authenticated` role are Supabase-provided; this test stubs minimal
equivalents to reproduce the same RLS behavior.
"""

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

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


async def _maintenance_conn(pg_cluster):
    return await asyncpg.connect(
        host=pg_cluster["socket_dir"],
        user=pg_cluster["admin_user"],
        database="postgres",
    )


async def _admin_conn(pg_cluster, dbname):
    return await asyncpg.connect(
        host=pg_cluster["socket_dir"],
        user=pg_cluster["admin_user"],
        database=dbname,
    )


async def _authenticated_conn(pg_cluster, dbname):
    return await asyncpg.connect(
        host=pg_cluster["socket_dir"],
        user="authenticated",
        database=dbname,
    )


@pytest_asyncio.fixture
async def seeded_db(pg_cluster):
    # Each test gets its own database — `authenticated` is a role (cluster-scoped,
    # created once in the pg_cluster fixture) but schemas/tables aren't, so reusing
    # one database across tests would leak state (and re-running AUTH_STUB_SQL's
    # `create schema auth` would fail outright on the second test).
    dbname = f"aa_test_{uuid.uuid4().hex}"
    maint = await _maintenance_conn(pg_cluster)
    try:
        await maint.execute(f'create database "{dbname}"')
    finally:
        await maint.close()

    admin = await _admin_conn(pg_cluster, dbname)
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)

        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        await admin.execute(
            "insert into auth.users (id) values ($1), ($2)", user_a, user_b
        )

        account_a = uuid.uuid4()
        account_b = uuid.uuid4()
        await admin.execute(
            """
            insert into public.accounts (id, user_id, institution, account_type)
            values ($1, $2, 'scotiabank', 'chequing'), ($3, $4, 'questrade', 'tfsa')
            """,
            account_a,
            user_a,
            account_b,
            user_b,
        )
        await admin.execute(
            """
            insert into public.users_profile
                (id, age, holdings_country, year_in_canada, risk_profile)
            values
                ($1, 29, 'CA', 2019, 'medium'),
                ($2, 40, 'CA', 2005, 'low')
            """,
            user_a,
            user_b,
        )
        await admin.execute(
            """
            insert into public.prices (ticker, date, close, source)
            values ('XEQT', '2026-08-31', 30.12, 'yfinance')
            """
        )

        yield {
            "dbname": dbname,
            "user_a": user_a,
            "user_b": user_b,
            "account_a": account_a,
            "account_b": account_b,
        }
    finally:
        await admin.close()
        maint = await _maintenance_conn(pg_cluster)
        try:
            await maint.execute(f'drop database "{dbname}"')
        finally:
            await maint.close()


async def test_user_sees_only_their_own_account(pg_cluster, seeded_db):
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with conn.transaction():
            await conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(seeded_db["user_a"])
            )
            rows = await conn.fetch("select id, user_id from public.accounts")
            assert [r["id"] for r in rows] == [seeded_db["account_a"]]
    finally:
        await conn.close()


async def test_cross_user_read_returns_zero_rows_for_the_other_users_data(pg_cluster, seeded_db):
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with conn.transaction():
            await conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(seeded_db["user_b"])
            )
            rows = await conn.fetch(
                "select id from public.accounts where id = $1", seeded_db["account_a"]
            )
            assert rows == []
    finally:
        await conn.close()


async def test_crafted_jwt_for_a_nonexistent_user_sees_nothing(pg_cluster, seeded_db):
    """Security-Model.md: cross-user reads return zero rows even with crafted JWTs."""
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with conn.transaction():
            await conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(uuid.uuid4())
            )
            accounts = await conn.fetch("select id from public.accounts")
            profiles = await conn.fetch("select id from public.users_profile")
            assert accounts == []
            assert profiles == []
    finally:
        await conn.close()


async def test_users_profile_is_scoped_by_its_own_id(pg_cluster, seeded_db):
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        async with conn.transaction():
            await conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(seeded_db["user_a"])
            )
            rows = await conn.fetch("select id from public.users_profile")
            assert [r["id"] for r in rows] == [seeded_db["user_a"]]
    finally:
        await conn.close()


async def test_insert_with_another_users_id_is_rejected(pg_cluster, seeded_db):
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        with pytest.raises(asyncpg.PostgresError, match="row-level security"):
            async with conn.transaction():
                await conn.execute(
                    "select set_config('request.jwt.claim.sub', $1, true)", str(seeded_db["user_a"])
                )
                await conn.execute(
                    """
                    insert into public.accounts (user_id, institution, account_type)
                    values ($1, 'kraken', 'crypto')
                    """,
                    seeded_db["user_b"],
                )
    finally:
        await conn.close()


async def test_reference_table_is_readable_by_anyone_but_not_writable(pg_cluster, seeded_db):
    conn = await _authenticated_conn(pg_cluster, seeded_db["dbname"])
    try:
        rows = await conn.fetch("select ticker from public.prices")
        assert [r["ticker"] for r in rows] == ["XEQT"]

        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                """
                insert into public.prices (ticker, date, close, source)
                values ('VFV', '2026-08-31', 100, 'yfinance')
                """
            )
    finally:
        await conn.close()
