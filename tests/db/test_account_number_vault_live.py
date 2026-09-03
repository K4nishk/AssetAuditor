"""Live proof of migration 0004's pgsodium column encryption (KCH-66 / AA-29).

pgsodium isn't installable on vanilla local Postgres — it's a Supabase-managed
extension, already present on every Supabase project — same limitation
tests/db/test_migration_0001_rls.py documents for the RLS test. That test
copes by stripping the `create extension pgsodium` line before applying the
migration; there is nothing meaningful left to test here once pgsodium calls
are stripped, so instead this file tries `create extension pgsodium` for
real against the ephemeral cluster and skips cleanly if it isn't available
(every sandbox except a real Supabase project), per CLAUDE.md: prove what's
provable locally, never fake a passing test.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_0001 = (REPO_ROOT / "app/db/migrations/0001_init.sql").read_text()
MIGRATION_0004 = (
    REPO_ROOT / "app/db/migrations/0004_account_number_vault_encryption.sql"
).read_text()

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


async def _admin_conn(pg_cluster, dbname):
    return await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=dbname
    )


async def _authenticated_conn_as(pg_cluster, dbname, user_id):
    conn = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user="authenticated", database=dbname
    )
    await conn.execute("select set_config('request.jwt.claim.sub', $1, false)", str(user_id))
    return conn


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    dbname = scratch_database
    admin = await _admin_conn(pg_cluster, dbname)
    try:
        try:
            await admin.execute("create extension if not exists pgsodium;")
        except asyncpg.PostgresError as exc:
            pytest.skip(f"pgsodium extension not available on this Postgres: {exc}")

        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(
            MIGRATION_0001.replace("create extension if not exists pgsodium;\n", "")
        )
        await admin.execute(MIGRATION_0004)

        user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", user_id)
        await admin.execute(
            """
            insert into public.users_profile
                (id, age, holdings_country, year_in_canada, risk_profile)
            values ($1, 35, 'CA', 2009, 'medium')
            """,
            user_id,
        )
        account_id = await admin.fetchval(
            """
            insert into public.accounts (user_id, institution, account_type)
            values ($1, 'questrade', 'tfsa')
            returning id
            """,
            user_id,
        )
        other_account_id = await admin.fetchval(
            """
            insert into public.accounts (user_id, institution, account_type)
            values ($1, 'wealthsimple', 'tfsa')
            returning id
            """,
            user_id,
        )
        yield {
            "dbname": dbname,
            "user_id": user_id,
            "account_id": account_id,
            "other_account_id": other_account_id,
        }
    finally:
        await admin.close()


async def test_store_then_reveal_round_trips(pg_cluster, seeded_db):
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        await conn.execute(
            "select public.vault_store_account_number($1, $2)",
            seeded_db["account_id"],
            "1234567890",
        )
        revealed = await conn.fetchval(
            "select public.vault_reveal_account_number($1)", seeded_db["account_id"]
        )
        assert revealed == "1234567890"
    finally:
        await conn.close()


async def test_ciphertext_at_rest_is_not_the_plaintext(pg_cluster, seeded_db):
    admin = await _admin_conn(pg_cluster, seeded_db["dbname"])
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        await conn.execute(
            "select public.vault_store_account_number($1, $2)",
            seeded_db["account_id"],
            "1234567890",
        )
        stored = await admin.fetchval(
            "select encrypted_account_number from public.account_number_vault"
            " where account_id = $1",
            seeded_db["account_id"],
        )
        assert stored is not None
        assert b"1234567890" not in bytes(stored)
    finally:
        await conn.close()
        await admin.close()


async def test_ciphertext_copied_onto_another_account_id_fails_to_decrypt(pg_cluster, seeded_db):
    """AEAD associated data binds ciphertext to its account_id row — copying a
    vault row's ciphertext onto a different account_id must not decrypt."""
    admin = await _admin_conn(pg_cluster, seeded_db["dbname"])
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        await conn.execute(
            "select public.vault_store_account_number($1, $2)",
            seeded_db["account_id"],
            "1234567890",
        )
        await conn.execute(
            "select public.vault_store_account_number($1, $2)",
            seeded_db["other_account_id"],
            "0000000000",
        )
        stolen_ciphertext = await admin.fetchval(
            "select encrypted_account_number from public.account_number_vault"
            " where account_id = $1",
            seeded_db["account_id"],
        )
        await admin.execute(
            """
            update public.account_number_vault
            set encrypted_account_number = $1
            where account_id = $2
            """,
            stolen_ciphertext,
            seeded_db["other_account_id"],
        )

        with pytest.raises(asyncpg.PostgresError):
            await conn.fetchval(
                "select public.vault_reveal_account_number($1)", seeded_db["other_account_id"]
            )
    finally:
        await conn.close()
        await admin.close()


async def test_direct_table_access_is_revoked_for_authenticated(pg_cluster, seeded_db):
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch("select * from public.account_number_vault")
    finally:
        await conn.close()


async def test_store_rejects_an_account_id_owned_by_another_user(pg_cluster, seeded_db):
    admin = await _admin_conn(pg_cluster, seeded_db["dbname"])
    try:
        other_user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", other_user_id)
        conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], other_user_id)
        try:
            with pytest.raises(asyncpg.RaiseError, match="not found for the current user"):
                await conn.execute(
                    "select public.vault_store_account_number($1, $2)",
                    seeded_db["account_id"],
                    "1234567890",
                )
        finally:
            await conn.close()
    finally:
        await admin.close()


async def test_reveal_returns_null_when_no_row_is_stored_for_the_account(pg_cluster, seeded_db):
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        revealed = await conn.fetchval(
            "select public.vault_reveal_account_number($1)", seeded_db["other_account_id"]
        )
        assert revealed is None
    finally:
        await conn.close()


async def test_store_is_denied_after_the_user_is_deactivated(pg_cluster, seeded_db):
    admin = await _admin_conn(pg_cluster, seeded_db["dbname"])
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        await admin.execute(
            "update public.users_profile set deactivated_at = now() where id = $1",
            seeded_db["user_id"],
        )

        with pytest.raises(asyncpg.RaiseError, match="not found for the current user"):
            await conn.execute(
                "select public.vault_store_account_number($1, $2)",
                seeded_db["account_id"],
                "1234567890",
            )
    finally:
        await conn.close()
        await admin.close()


async def test_reveal_is_denied_after_the_user_is_deactivated(pg_cluster, seeded_db):
    admin = await _admin_conn(pg_cluster, seeded_db["dbname"])
    conn = await _authenticated_conn_as(pg_cluster, seeded_db["dbname"], seeded_db["user_id"])
    try:
        await conn.execute(
            "select public.vault_store_account_number($1, $2)",
            seeded_db["account_id"],
            "1234567890",
        )
        await admin.execute(
            "update public.users_profile set deactivated_at = now() where id = $1",
            seeded_db["user_id"],
        )

        revealed = await conn.fetchval(
            "select public.vault_reveal_account_number($1)", seeded_db["account_id"]
        )
        assert revealed is None
    finally:
        await conn.close()
        await admin.close()
