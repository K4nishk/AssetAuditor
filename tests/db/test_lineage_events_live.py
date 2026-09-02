"""Live proof of the `lineage_events` writes (KCH-48 / AA-13).

Same ephemeral-Postgres approach as tests/db/test_etl_jobs_queue.py: the
`event_type` check constraint and the `(user_id, job_id)` FK to `etl_jobs`
are Postgres guarantees, not something a mock connection can prove. The
worker connects as service_role (bypasses RLS) — approximated here by the
cluster's superuser, same convention as tests/db/test_etl_jobs_queue.py.
Skips cleanly wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from worker.lineage import LineageEmitter, emit_lineage_event

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

        job_id = uuid.uuid4()
        await admin.execute(
            "insert into public.etl_jobs (id, user_id, bronze_file_id) values ($1, $2, $3)",
            job_id,
            user_id,
            bronze_id,
        )

        yield {"conn": admin, "user_id": user_id, "job_id": job_id}
    finally:
        await admin.close()


async def test_emit_lineage_event_persists_a_start_row(seeded_db):
    conn = seeded_db["conn"]

    row = await emit_lineage_event(
        conn,
        user_id=str(seeded_db["user_id"]),
        run_id=str(uuid.uuid4()),
        job_id=str(seeded_db["job_id"]),
        step="extract",
        event_type="START",
        facets={"extraction_method": "pdfplumber"},
    )

    assert row["event_type"] == "START"
    assert row["occurred_at"] is not None
    stored = await conn.fetchrow(
        "select facets, payload from public.lineage_events where id = $1", row["id"]
    )
    assert stored["facets"] == '{"extraction_method": "pdfplumber"}'


async def test_lineage_emitter_start_and_complete_share_a_run_id(seeded_db):
    conn = seeded_db["conn"]
    emitter = LineageEmitter(
        conn, user_id=str(seeded_db["user_id"]), job_id=str(seeded_db["job_id"])
    )

    await emitter.start("mask", facets={"masking_applied": True})
    await emitter.complete("mask", facets={"masking_applied": True})

    rows = await conn.fetch(
        """
        select event_type, run_id from public.lineage_events
        where job_id = $1 order by occurred_at
        """,
        seeded_db["job_id"],
    )
    assert [r["event_type"] for r in rows] == ["START", "COMPLETE"]
    assert rows[0]["run_id"] == rows[1]["run_id"] == uuid.UUID(emitter.run_id)


async def test_lineage_events_rejects_an_invalid_event_type_at_the_db(seeded_db):
    conn = seeded_db["conn"]

    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            """
            insert into public.lineage_events (user_id, run_id, job_id, event_type)
            values ($1, $2, $3, 'RUNNING')
            """,
            seeded_db["user_id"],
            uuid.uuid4(),
            seeded_db["job_id"],
        )


async def test_lineage_events_rejects_a_job_id_for_a_different_user(seeded_db):
    conn = seeded_db["conn"]
    other_user_id = uuid.uuid4()
    await conn.execute("insert into auth.users (id) values ($1)", other_user_id)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await emit_lineage_event(
            conn,
            user_id=str(other_user_id),
            run_id=str(uuid.uuid4()),
            job_id=str(seeded_db["job_id"]),
            step="extract",
            event_type="START",
        )


async def test_job_id_is_nulled_out_when_the_etl_job_is_deleted(seeded_db):
    conn = seeded_db["conn"]
    row = await emit_lineage_event(
        conn,
        user_id=str(seeded_db["user_id"]),
        run_id=str(uuid.uuid4()),
        job_id=str(seeded_db["job_id"]),
        step="extract",
        event_type="START",
    )

    await conn.execute("delete from public.etl_jobs where id = $1", seeded_db["job_id"])

    survivor = await conn.fetchrow(
        "select job_id from public.lineage_events where id = $1", row["id"]
    )
    assert survivor["job_id"] is None
