"""Live proof of the drill-down queries (KCH-60 / AA-23).

Same ephemeral-Postgres approach as tests/db/test_lineage_events_live.py:
seeds a full chain — bronze_files -> etl_jobs -> staged_rows, a
`lineage_events` row binding a run_id to that job, and gold rows
(`term_buckets`/`diversification_cuts`/`networth_snapshots`) stamped with
that same run_id — then walks it with `app.db.queries.lineage_slice`
exactly as `app.routes.lineage` does. Skips cleanly wherever Postgres
tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import lineage_slice
from worker.lineage import emit_lineage_event

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

SNAPSHOT_DATE = date(2026, 7, 31)

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
            insert into public.bronze_files (id, user_id, sha256, institution, period, blob_url)
            values ($1, $2, $3, 'questrade', '2026-06', 'https://blob.example/x')
            """,
            bronze_id,
            user_id,
            uuid.uuid4().hex + uuid.uuid4().hex,
        )

        job_id = uuid.uuid4()
        await admin.execute(
            "insert into public.etl_jobs (id, user_id, bronze_file_id, status) "
            "values ($1, $2, $3, 'done')",
            job_id,
            user_id,
            bronze_id,
        )

        staged_row_id = uuid.uuid4()
        await admin.execute(
            """
            insert into public.staged_rows
                (id, user_id, job_id, entity, payload, method, confirmed_at)
            values ($1, $2, $3, 'transaction', '{"amount": "10.00"}'::jsonb,
                    'deterministic', now())
            """,
            staged_row_id,
            user_id,
            job_id,
        )

        run_id = uuid.uuid4()
        await emit_lineage_event(
            admin,
            user_id=str(user_id),
            run_id=str(run_id),
            job_id=str(job_id),
            step="confirm",
            event_type="COMPLETE",
        )

        await admin.execute(
            """
            insert into public.term_buckets (user_id, snapshot_date, bucket, amount_cad, run_id)
            values ($1, $2, 'short_term', 100.00, $3)
            """,
            user_id,
            SNAPSHOT_DATE,
            run_id,
        )
        await admin.execute(
            """
            insert into public.diversification_cuts
                (user_id, snapshot_date, cut, label, amount_cad, run_id)
            values ($1, $2, 'institution', 'questrade', 100.00, $3)
            """,
            user_id,
            SNAPSHOT_DATE,
            run_id,
        )
        await admin.execute(
            """
            insert into public.networth_snapshots
                (user_id, snapshot_date, total_assets_cad, total_liabilities_cad,
                 net_worth_cad, run_id)
            values ($1, $2, 100.00, 0.00, 100.00, $3)
            """,
            user_id,
            SNAPSHOT_DATE,
            run_id,
        )

        yield {
            "conn": admin,
            "user_id": user_id,
            "job_id": job_id,
            "bronze_id": bronze_id,
            "run_id": run_id,
        }
    finally:
        await admin.close()


async def test_find_run_id_for_term_bucket(seeded_db):
    run_id = await lineage_slice.find_run_id_for_term_bucket(
        seeded_db["conn"],
        user_id=str(seeded_db["user_id"]),
        snapshot_date=SNAPSHOT_DATE,
        bucket="short_term",
    )
    assert run_id == str(seeded_db["run_id"])


async def test_find_run_id_for_term_bucket_returns_none_for_an_unknown_bucket(seeded_db):
    run_id = await lineage_slice.find_run_id_for_term_bucket(
        seeded_db["conn"],
        user_id=str(seeded_db["user_id"]),
        snapshot_date=SNAPSHOT_DATE,
        bucket="long_term",
    )
    assert run_id is None


async def test_find_run_id_for_diversification_cut(seeded_db):
    run_id = await lineage_slice.find_run_id_for_diversification_cut(
        seeded_db["conn"],
        user_id=str(seeded_db["user_id"]),
        snapshot_date=SNAPSHOT_DATE,
        cut="institution",
        label="questrade",
    )
    assert run_id == str(seeded_db["run_id"])


async def test_find_run_id_for_net_worth(seeded_db):
    run_id = await lineage_slice.find_run_id_for_net_worth(
        seeded_db["conn"], user_id=str(seeded_db["user_id"]), snapshot_date=SNAPSHOT_DATE
    )
    assert run_id == str(seeded_db["run_id"])


async def test_find_job_id_for_run_walks_back_to_the_confirming_job(seeded_db):
    job_id = await lineage_slice.find_job_id_for_run(
        seeded_db["conn"], user_id=str(seeded_db["user_id"]), run_id=str(seeded_db["run_id"])
    )
    assert job_id == str(seeded_db["job_id"])


async def test_find_job_id_for_run_returns_none_for_an_unknown_run(seeded_db):
    job_id = await lineage_slice.find_job_id_for_run(
        seeded_db["conn"], user_id=str(seeded_db["user_id"]), run_id=str(uuid.uuid4())
    )
    assert job_id is None


async def test_get_bronze_file_returns_the_source_file(seeded_db):
    bronze = await lineage_slice.get_bronze_file(
        seeded_db["conn"],
        user_id=str(seeded_db["user_id"]),
        bronze_file_id=str(seeded_db["bronze_id"]),
    )
    assert bronze["institution"] == "questrade"
    assert bronze["purged_at"] is None


async def test_queries_scope_to_the_requesting_user(seeded_db):
    other_user_id = uuid.uuid4()
    await seeded_db["conn"].execute("insert into auth.users (id) values ($1)", other_user_id)

    run_id = await lineage_slice.find_run_id_for_term_bucket(
        seeded_db["conn"],
        user_id=str(other_user_id),
        snapshot_date=SNAPSHOT_DATE,
        bucket="short_term",
    )
    assert run_id is None
