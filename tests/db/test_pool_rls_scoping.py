"""Live proof that `app.db.pool.rls_connection` actually scopes queries to a user.

Same ephemeral-Postgres approach as test_migration_0001_rls.py (real Postgres,
not a mock, because RLS is a Postgres-enforced guarantee) but exercising our
own pool wiring end to end: connect via the pool's DSN, `rls_connection(user)`
should make `auth.uid()` resolve inside the `authenticated` role exactly the
way migration 0001's policies expect, and release cleanly so the next
`rls_connection` call for a different user starts from a clean slate.

Skips cleanly wherever initdb/pg_ctl aren't installed (see tests/db/conftest.py).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db import pool as pool_module

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


def _dsn(pg_cluster, dbname: str) -> str:
    # Unix-socket DSN: asyncpg (like libpq) accepts a `host` query param
    # pointing at the socket directory instead of a TCP host.
    return f"postgresql://{pg_cluster['admin_user']}@/{dbname}?host={pg_cluster['socket_dir']}"


@pytest_asyncio.fixture
async def seeded_dsn(pg_cluster, scratch_database, monkeypatch):
    dbname = scratch_database
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=dbname
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)

        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1), ($2)", user_a, user_b)

        account_a = uuid.uuid4()
        await admin.execute(
            """
            insert into public.accounts (id, user_id, institution, account_type)
            values ($1, $2, 'scotiabank', 'chequing')
            """,
            account_a,
            user_a,
        )
    finally:
        await admin.close()

    dsn = _dsn(pg_cluster, dbname)
    monkeypatch.setenv("DATABASE_URL", dsn)

    try:
        yield {"user_a": user_a, "user_b": user_b, "account_a": account_a}
    finally:
        await pool_module.close_pool()


async def test_rls_connection_scopes_reads_to_the_given_user(seeded_dsn):
    async with pool_module.rls_connection(str(seeded_dsn["user_a"])) as conn:
        rows = await conn.fetch("select id from public.accounts")
        assert [r["id"] for r in rows] == [seeded_dsn["account_a"]]


async def test_rls_connection_hides_other_users_rows(seeded_dsn):
    async with pool_module.rls_connection(str(seeded_dsn["user_b"])) as conn:
        rows = await conn.fetch(
            "select id from public.accounts where id = $1", seeded_dsn["account_a"]
        )
        assert rows == []


async def test_pooled_connection_does_not_leak_identity_across_acquisitions(seeded_dsn):
    """A physical connection reused for a different user must not retain the old role/claim."""
    async with pool_module.rls_connection(str(seeded_dsn["user_a"])) as conn:
        rows = await conn.fetch("select id from public.accounts")
        assert len(rows) == 1

    async with pool_module.rls_connection(str(seeded_dsn["user_b"])) as conn:
        rows = await conn.fetch("select id from public.accounts")
        assert rows == []

        role = await conn.fetchval("select current_setting('request.jwt.claim.sub', true)")
        assert role == str(seeded_dsn["user_b"])
