"""Live proof of `worker.retention`'s sweeps (KCH-54 / AA-19).

Same ephemeral-Postgres approach as tests/db/test_lineage_events_live.py: the
`blob_url not null` constraint, the `staged_rows`/`etl_jobs` FK cascade, and
the `lineage_events` check constraint are Postgres guarantees a fake
connection can't prove. Skips cleanly wherever Postgres tooling is
unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from worker.retention import run_retention_sweep

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

_NOW = datetime(2026, 9, 2, tzinfo=UTC)


class FakeBlob:
    def __init__(self):
        self.deleted: list[str] = []

    def put(self, pathname, data, content_type):  # pragma: no cover - unused
        raise NotImplementedError

    def delete(self, url: str) -> None:
        self.deleted.append(url)


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

        old_bronze_id = uuid.uuid4()
        old_created_at = _NOW - timedelta(days=15)
        await admin.execute(
            """
            insert into public.bronze_files
                (id, user_id, sha256, institution, blob_url, created_at)
            values ($1, $2, $3, 'questrade', 'https://blob.example/old', $4)
            """,
            old_bronze_id,
            user_id,
            uuid.uuid4().hex + uuid.uuid4().hex,
            old_created_at,
        )

        fresh_bronze_id = uuid.uuid4()
        await admin.execute(
            """
            insert into public.bronze_files (id, user_id, sha256, institution, blob_url)
            values ($1, $2, $3, 'questrade', 'https://blob.example/fresh')
            """,
            fresh_bronze_id,
            user_id,
            uuid.uuid4().hex + uuid.uuid4().hex,
        )

        old_job_id = uuid.uuid4()
        old_updated_at = _NOW - timedelta(days=5)
        await admin.execute(
            """
            insert into public.etl_jobs
                (id, user_id, bronze_file_id, status, error, created_at, updated_at)
            values ($1, $2, $3, 'failed', 'boom: parse error', $4, $4)
            """,
            old_job_id,
            user_id,
            fresh_bronze_id,
            old_updated_at,
        )

        # A confirmed staged_rows child proves the job row survives the sweep
        # (a hard delete of etl_jobs would cascade and destroy this).
        await admin.execute(
            """
            insert into public.staged_rows
                (user_id, job_id, entity, payload, method, confirmed_at)
            values ($1, $2, 'transaction', '{}'::jsonb, 'deterministic', now())
            """,
            user_id,
            old_job_id,
        )

        yield {
            "conn": admin,
            "user_id": user_id,
            "old_bronze_id": old_bronze_id,
            "fresh_bronze_id": fresh_bronze_id,
            "old_job_id": old_job_id,
        }
    finally:
        await admin.close()


async def test_run_retention_sweep_purges_old_bronze_and_tombstones_it(seeded_db):
    conn = seeded_db["conn"]
    blob = FakeBlob()

    result = await run_retention_sweep(conn, blob=blob, now=_NOW)

    assert result.bronze_purged == 1
    assert blob.deleted == ["https://blob.example/old"]

    old_row = await conn.fetchrow(
        "select purged_at, blob_url from public.bronze_files where id = $1",
        seeded_db["old_bronze_id"],
    )
    assert old_row["purged_at"] == _NOW
    assert old_row["blob_url"] == ""

    tombstones = await conn.fetch(
        """
        select event_type, facets from public.lineage_events
        where user_id = $1 and facets->>'bronze_file_id' = $2
        order by occurred_at
        """,
        seeded_db["user_id"],
        str(seeded_db["old_bronze_id"]),
    )
    assert [t["event_type"] for t in tombstones] == ["START", "COMPLETE"]


async def test_run_retention_sweep_leaves_a_fresh_bronze_file_alone(seeded_db):
    conn = seeded_db["conn"]

    await run_retention_sweep(conn, blob=FakeBlob(), now=_NOW)

    fresh_row = await conn.fetchrow(
        "select purged_at from public.bronze_files where id = $1", seeded_db["fresh_bronze_id"]
    )
    assert fresh_row["purged_at"] is None


async def test_run_retention_sweep_scrubs_old_job_error_but_keeps_the_row_and_its_staged_rows(
    seeded_db,
):
    conn = seeded_db["conn"]

    result = await run_retention_sweep(conn, blob=FakeBlob(), now=_NOW)

    assert result.job_logs_scrubbed == 1

    job_row = await conn.fetchrow(
        "select status, error from public.etl_jobs where id = $1", seeded_db["old_job_id"]
    )
    assert job_row["status"] == "failed"
    assert job_row["error"] is None

    staged_count = await conn.fetchval(
        "select count(*) from public.staged_rows where job_id = $1", seeded_db["old_job_id"]
    )
    assert staged_count == 1
