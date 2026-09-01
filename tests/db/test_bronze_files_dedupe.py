"""Live proof that `bronze_files` dedupes by (user_id, sha256) (KCH-46 / AA-11).

`unique (user_id, sha256)` (migration 0001) is a Postgres constraint — this
proves `insert_bronze_file`'s `ON CONFLICT DO NOTHING` actually closes the
race a plain check-then-insert would leave open, which a mocked connection
can't demonstrate. Same ephemeral-Postgres approach as
tests/db/test_etl_jobs_queue.py; skips cleanly where Postgres tooling is
unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.bronze_files import find_by_sha256, insert_bronze_file

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


async def test_insert_bronze_file_returns_the_new_row(seeded_db):
    conn = seeded_db["conn"]

    row = await insert_bronze_file(
        conn,
        user_id=seeded_db["user_id"],
        sha256_hex="a" * 64,
        institution="scotiabank",
        period="2026-07",
        blob_url="https://blob.example/bronze/a",
    )

    assert row["sha256"] == "a" * 64
    assert row["blob_url"] == "https://blob.example/bronze/a"


async def test_insert_bronze_file_returns_none_on_duplicate_sha256(seeded_db):
    conn = seeded_db["conn"]
    await insert_bronze_file(
        conn,
        user_id=seeded_db["user_id"],
        sha256_hex="a" * 64,
        institution="scotiabank",
        period="2026-07",
        blob_url="https://blob.example/bronze/a",
    )

    duplicate = await insert_bronze_file(
        conn,
        user_id=seeded_db["user_id"],
        sha256_hex="a" * 64,
        institution="scotiabank",
        period="2026-08",
        blob_url="https://blob.example/bronze/a-again",
    )

    assert duplicate is None
    rows = await conn.fetch("select id from public.bronze_files")
    assert len(rows) == 1


async def test_find_by_sha256_ignores_purged_rows(seeded_db):
    conn = seeded_db["conn"]
    inserted = await insert_bronze_file(
        conn,
        user_id=seeded_db["user_id"],
        sha256_hex="a" * 64,
        institution="scotiabank",
        period="2026-07",
        blob_url="https://blob.example/bronze/a",
    )
    await conn.execute(
        "update public.bronze_files set purged_at = now() where id = $1", inserted["id"]
    )

    found = await find_by_sha256(conn, user_id=seeded_db["user_id"], sha256_hex="a" * 64)

    assert found is None
