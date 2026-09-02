"""Live proof of `app.db.queries.account_lifecycle`'s deactivate/reactivate and
hard-purge behaviour (KCH-45 / AA-10).

Same ephemeral-Postgres approach as tests/db/test_gold_rebuild_live.py: the
FK-cascade purge (`accounts` -> `account_number_vault`/`holdings` ->
`lots`/`transactions`, `bronze_files` -> `etl_jobs` -> `staged_rows`) and the
`lineage_events` redaction (pgcrypto `digest()`, `run_id != $2` exclusion)
are real Postgres behaviour a mocked connection can't prove. Skips cleanly
wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.account_lifecycle import (
    deactivate_account,
    get_deactivation_status,
    purge_user_rows,
    reactivate_account,
    redact_lineage_events,
    unpurged_bronze_blob_urls,
)
from worker.lineage import emit_lineage_event, new_run_id

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


async def _insert_profile(conn, *, user_id):
    await conn.execute(
        """
        insert into public.users_profile (id, age, holdings_country, year_in_canada, risk_profile)
        values ($1, 35, 'CA', 2009, 'medium')
        """,
        user_id,
    )


# --- deactivate / reactivate ----------------------------------------------


async def test_deactivate_then_reactivate_round_trips(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await _insert_profile(conn, user_id=user_id)

    deactivated = await deactivate_account(conn, user_id=user_id)
    assert deactivated["deactivated_at"] is not None

    status = await get_deactivation_status(conn, user_id=user_id)
    assert status["deactivated_at"] is not None

    reactivated = await reactivate_account(conn, user_id=user_id)
    assert reactivated["deactivated_at"] is None

    status = await get_deactivation_status(conn, user_id=user_id)
    assert status["deactivated_at"] is None


async def test_deactivate_is_idempotent_and_returns_none_the_second_time(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await _insert_profile(conn, user_id=user_id)

    first = await deactivate_account(conn, user_id=user_id)
    second = await deactivate_account(conn, user_id=user_id)

    assert first is not None
    assert second is None
    status = await get_deactivation_status(conn, user_id=user_id)
    assert status["deactivated_at"] is not None


async def test_get_deactivation_status_is_none_when_no_profile_exists(seeded_db):
    status = await get_deactivation_status(seeded_db["conn"], user_id=seeded_db["user_id"])
    assert status is None


# --- hard purge -------------------------------------------------------------


async def _seed_full_account(conn, *, user_id):
    await _insert_profile(conn, user_id=user_id)

    account_id = await conn.fetchval(
        """
        insert into public.accounts (user_id, institution, account_type, masked_identifier)
        values ($1, 'questrade', 'tfsa', 'qt-...9033')
        returning id
        """,
        user_id,
    )
    await conn.execute(
        """
        insert into public.account_number_vault (user_id, account_id, encrypted_account_number)
        values ($1, $2, 'x'::bytea)
        """,
        user_id,
        account_id,
    )
    holding_id = await conn.fetchval(
        """
        insert into public.holdings (user_id, account_id, ticker, quantity)
        values ($1, $2, 'XEQT', 10)
        returning id
        """,
        user_id,
        account_id,
    )
    await conn.execute(
        """
        insert into public.lots (user_id, holding_id, quantity)
        values ($1, $2, 10)
        """,
        user_id,
        holding_id,
    )
    await conn.execute(
        """
        insert into public.transactions
            (user_id, account_id, occurred_at, kind, amount, currency)
        values ($1, $2, now(), 'buy', 100.00, 'CAD')
        """,
        user_id,
        account_id,
    )
    await conn.execute(
        """
        insert into public.liabilities (user_id, kind, balance)
        values ($1, 'mortgage', 5000.00)
        """,
        user_id,
    )
    await conn.execute(
        """
        insert into public.room_events (user_id, account_type, year, kind, amount)
        values ($1, 'tfsa', 2026, 'grant', 7000.00)
        """,
        user_id,
    )
    await conn.execute(
        """
        insert into public.networth_snapshots
            (user_id, snapshot_date, total_assets_cad, total_liabilities_cad, net_worth_cad, run_id)
        values ($1, current_date, 1000.00, 0, 1000.00, gen_random_uuid())
        """,
        user_id,
    )
    await conn.execute(
        """
        insert into public.term_buckets (user_id, snapshot_date, bucket, amount_cad, run_id)
        values ($1, current_date, 'short_term', 1000.00, gen_random_uuid())
        """,
        user_id,
    )
    await conn.execute(
        """
        insert into public.diversification_cuts
            (user_id, snapshot_date, cut, label, amount_cad, run_id)
        values ($1, current_date, 'class', 'equity', 1000.00, gen_random_uuid())
        """,
        user_id,
    )

    bronze_id = await conn.fetchval(
        """
        insert into public.bronze_files (user_id, sha256, blob_url)
        values ($1, 'a' || repeat('0', 63), 'https://blob.example/bronze/u/1')
        returning id
        """,
        user_id,
    )
    job_id = await conn.fetchval(
        """
        insert into public.etl_jobs (user_id, bronze_file_id) values ($1, $2) returning id
        """,
        user_id,
        bronze_id,
    )
    await conn.execute(
        """
        insert into public.staged_rows (user_id, job_id, entity, payload, method)
        values ($1, $2, 'transaction', '{}'::jsonb, 'deterministic')
        """,
        user_id,
        job_id,
    )

    prior_run_id = new_run_id()
    await emit_lineage_event(
        conn,
        user_id=user_id,
        run_id=prior_run_id,
        job_id=job_id,
        step="extract",
        event_type="COMPLETE",
        facets={"extraction_method": "pdfplumber", "institution": "questrade"},
    )
    return {"account_id": account_id, "prior_run_id": prior_run_id}


async def test_purge_user_rows_cascades_every_table(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await _seed_full_account(conn, user_id=user_id)

    counts = await purge_user_rows(conn, user_id=user_id)

    assert counts.accounts == 1
    assert counts.bronze_files == 1
    assert counts.liabilities == 1
    assert counts.room_events == 1
    assert counts.networth_snapshots == 1
    assert counts.term_buckets == 1
    assert counts.diversification_cuts == 1
    assert counts.profile == 1

    assert await conn.fetchval(
        "select count(*) from public.accounts where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.account_number_vault where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.holdings where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval("select count(*) from public.lots where user_id = $1", user_id) == 0
    assert await conn.fetchval(
        "select count(*) from public.transactions where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.liabilities where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.room_events where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.bronze_files where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.etl_jobs where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.staged_rows where user_id = $1", user_id
    ) == 0
    assert await conn.fetchval(
        "select count(*) from public.users_profile where id = $1", user_id
    ) == 0


async def test_purge_user_rows_does_not_delete_lineage_events(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    seed = await _seed_full_account(conn, user_id=user_id)

    await purge_user_rows(conn, user_id=user_id)

    row = await conn.fetchrow(
        "select job_id, run_id from public.lineage_events where user_id = $1", user_id
    )
    assert row is not None
    assert row["run_id"] == uuid.UUID(seed["prior_run_id"])
    assert row["job_id"] is None  # etl_jobs cascade sets this null, row itself survives


async def test_unpurged_bronze_blob_urls_excludes_already_purged_rows(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await _seed_full_account(conn, user_id=user_id)
    await conn.execute(
        "update public.bronze_files set purged_at = now(), blob_url = '' where user_id = $1",
        user_id,
    )

    urls = await unpurged_bronze_blob_urls(conn, user_id=user_id)

    assert urls == []


async def test_redact_lineage_events_scrubs_payload_to_a_hash_stub_but_keeps_the_current_run(
    seeded_db,
):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    seed = await _seed_full_account(conn, user_id=user_id)

    deletion_run_id = new_run_id()
    await emit_lineage_event(
        conn,
        user_id=user_id,
        run_id=deletion_run_id,
        step="account_deletion",
        event_type="START",
    )

    redacted_count = await redact_lineage_events(
        conn, user_id=user_id, keep_run_id=deletion_run_id
    )
    assert redacted_count == 1  # only the prior `extract` event, not this run's START

    rows = {
        str(row["run_id"]): row
        for row in await conn.fetch(
            "select run_id, payload, facets from public.lineage_events where user_id = $1",
            user_id,
        )
    }

    prior_row = rows[seed["prior_run_id"]]
    prior_payload = json.loads(prior_row["payload"])
    assert prior_payload["redacted"] is True
    assert len(prior_payload["sha256"]) == 64
    assert "extraction_method" not in json.dumps(prior_payload)

    deletion_row = rows[deletion_run_id]
    deletion_payload = json.loads(deletion_row["payload"])
    assert deletion_payload.get("redacted") is not True
    assert deletion_payload["eventType"] == "START"


async def test_redact_lineage_events_is_idempotent(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    await _seed_full_account(conn, user_id=user_id)
    run_id = new_run_id()

    first = await redact_lineage_events(conn, user_id=user_id, keep_run_id=run_id)
    second = await redact_lineage_events(conn, user_id=user_id, keep_run_id=run_id)

    assert first == 1
    assert second == 0
