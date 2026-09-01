"""Live proof of the `etl_jobs` claim primitive (KCH-46 / AA-11).

Same ephemeral-Postgres approach as tests/db/test_pool_rls_scoping.py: `FOR
UPDATE SKIP LOCKED` is a Postgres locking guarantee, not something a mock
connection can prove. The worker connects as service_role (bypasses RLS) —
approximated here by the cluster's superuser, same convention as
tests/db/test_worker_heartbeat_live.py. Skips cleanly wherever Postgres
tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from worker.queue import claim_next_job, count_queued_jobs, release_job

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

        bronze_ids = []
        for _ in range(2):
            bronze_id = uuid.uuid4()
            await admin.execute(
                """
                insert into public.bronze_files (id, user_id, sha256, blob_url)
                values ($1, $2, $3, 'https://blob.example/x')
                """,
                bronze_id,
                user_id,
                uuid.uuid4().hex + uuid.uuid4().hex,
            )
            bronze_ids.append(bronze_id)

        job_ids = []
        for bronze_id in bronze_ids:
            job_id = uuid.uuid4()
            await admin.execute(
                """
                insert into public.etl_jobs (id, user_id, bronze_file_id)
                values ($1, $2, $3)
                """,
                job_id,
                user_id,
                bronze_id,
            )
            job_ids.append(job_id)

        yield {
            "conn": admin,
            "dbname": scratch_database,
            "user_id": user_id,
            "job_ids": job_ids,
        }
    finally:
        await admin.close()


async def test_claim_next_job_claims_the_oldest_pending_job(seeded_db):
    conn = seeded_db["conn"]

    claimed = await claim_next_job(conn, claimed_by="worker-a")

    assert claimed["id"] == seeded_db["job_ids"][0]
    assert claimed["status"] == "claimed"
    assert claimed["claimed_by"] == "worker-a"
    assert claimed["claimed_at"] is not None


async def test_claim_next_job_never_claims_the_same_row_twice(seeded_db):
    conn = seeded_db["conn"]

    first = await claim_next_job(conn, claimed_by="worker-a")
    second = await claim_next_job(conn, claimed_by="worker-a")
    third = await claim_next_job(conn, claimed_by="worker-a")

    assert {first["id"], second["id"]} == set(seeded_db["job_ids"])
    assert third is None


async def test_for_update_skip_locked_lets_a_second_worker_claim_a_different_row(
    pg_cluster, seeded_db
):
    """Two workers claiming concurrently must each get a distinct row, not block."""
    second_conn = await asyncpg.connect(
        host=pg_cluster["socket_dir"],
        user=pg_cluster["admin_user"],
        database=seeded_db["dbname"],
    )
    try:
        tx1 = seeded_db["conn"].transaction()
        await tx1.start()
        claimed_first = await claim_next_job(seeded_db["conn"], claimed_by="worker-a")

        tx2 = second_conn.transaction()
        await tx2.start()
        claimed_second = await claim_next_job(second_conn, claimed_by="worker-b")

        assert claimed_first["id"] != claimed_second["id"]
        assert {claimed_first["id"], claimed_second["id"]} == set(seeded_db["job_ids"])

        await tx1.commit()
        await tx2.commit()
    finally:
        await second_conn.close()


async def test_release_job_transitions_status_and_records_error(seeded_db):
    conn = seeded_db["conn"]
    claimed = await claim_next_job(conn, claimed_by="worker-a")

    released = await release_job(
        conn,
        job_id=str(claimed["id"]),
        claimed_by="worker-a",
        expected_status="claimed",
        status="failed",
        error="magic-byte mismatch",
    )

    assert released["status"] == "failed"
    assert released["error"] == "magic-byte mismatch"


async def test_release_job_is_a_noop_for_a_non_owning_worker(seeded_db):
    conn = seeded_db["conn"]
    claimed = await claim_next_job(conn, claimed_by="worker-a")

    released = await release_job(
        conn,
        job_id=str(claimed["id"]),
        claimed_by="worker-b",
        expected_status="claimed",
        status="failed",
        error="stolen",
    )

    assert released is None
    row = await conn.fetchrow(
        "select status, error from public.etl_jobs where id = $1", claimed["id"]
    )
    assert row["status"] == "claimed"
    assert row["error"] is None


async def test_count_queued_jobs_counts_only_pending_rows(seeded_db):
    conn = seeded_db["conn"]
    assert await count_queued_jobs(conn) == len(seeded_db["job_ids"])

    await claim_next_job(conn, claimed_by="worker-a")

    assert await count_queued_jobs(conn) == len(seeded_db["job_ids"]) - 1
