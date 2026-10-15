"""Waiting jobs (AA-63, #46; spec #40 decision 8; owner decision R4.1) — live Postgres.

A job no backend can serve goes back to `pending` with a wait reason and a
`not_before` that backs off exponentially (1, 2, 4, 8, 16, 32, then every 60
minutes); `claim_next_job` skips it until then, and `early_wake` clears
`not_before` when the local model turns ready.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import etl_jobs
from tests.db.test_etl_jobs_queue import AUTH_STUB_SQL, MIGRATION_0005_SQL, MIGRATION_SQL_LOCAL
from worker.queue import claim_next_job, defer_job, early_wake

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(pg_cluster, scratch_database):
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)
        await admin.execute(MIGRATION_0005_SQL)
        users = [uuid.uuid4(), uuid.uuid4()]
        job_ids, bronze_ids = [], []
        for user_id in users:
            await admin.execute("insert into auth.users (id) values ($1)", user_id)
            bronze_id, job_id = uuid.uuid4(), uuid.uuid4()
            await admin.execute(
                "insert into public.bronze_files (id, user_id, sha256, blob_url)"
                " values ($1, $2, $3, 'https://blob.example/x')",
                bronze_id,
                user_id,
                uuid.uuid4().hex + uuid.uuid4().hex,
            )
            await admin.execute(
                "insert into public.etl_jobs (id, user_id, bronze_file_id) values ($1, $2, $3)",
                job_id,
                user_id,
                bronze_id,
            )
            job_ids.append(job_id)
            bronze_ids.append(bronze_id)
        yield {
            "conn": admin,
            "dbname": scratch_database,
            "users": users,
            "job_ids": job_ids,
            "bronze_ids": bronze_ids,
        }
    finally:
        await admin.close()


async def _defer_first(conn, reason: str = "local_not_ready"):
    job = await claim_next_job(conn, claimed_by="worker-a")
    return await defer_job(
        conn, job_id=job["id"], claimed_by="worker-a", expected_status="claimed", reason=reason
    )


async def test_defer_returns_an_owned_job_to_pending_with_its_reason(db):
    row = await _defer_first(db["conn"])
    assert row["status"] == "pending"
    assert row["wait_reason"] == "local_not_ready"
    assert row["defer_count"] == 1
    assert row["claimed_by"] is None
    assert 59 <= row["wait_seconds"] <= 61


async def test_backoff_doubles_then_caps_at_sixty_minutes(db):
    conn = db["conn"]
    minutes = []
    for _ in range(8):
        await early_wake(conn)
        job = None
        while job is None or job["id"] != db["job_ids"][0]:
            job = await claim_next_job(conn, claimed_by="worker-a")
            if job is not None and job["id"] != db["job_ids"][0]:
                await defer_job(
                    conn,
                    job_id=job["id"],
                    claimed_by="worker-a",
                    expected_status="claimed",
                    reason="local_not_ready",
                )
        row = await defer_job(
            conn,
            job_id=job["id"],
            claimed_by="worker-a",
            expected_status="claimed",
            reason="egress_unavailable",
        )
        minutes.append(round(row["wait_seconds"] / 60))
    assert minutes == [1, 2, 4, 8, 16, 32, 60, 60]


async def test_claim_skips_a_waiting_job_until_its_not_before(db):
    conn = db["conn"]
    deferred = await _defer_first(conn)
    other = await claim_next_job(conn, claimed_by="worker-a")
    assert other["id"] != deferred["id"]
    assert await claim_next_job(conn, claimed_by="worker-a") is None


async def test_early_wake_makes_waiting_jobs_claimable_now(db):
    conn = db["conn"]
    deferred = await _defer_first(conn)
    assert await early_wake(conn) == 1
    job = await claim_next_job(conn, claimed_by="worker-a")
    assert job["id"] == deferred["id"]


async def test_claim_after_the_wait_clears_the_reason_and_not_before(db):
    conn = db["conn"]
    deferred = await _defer_first(conn)
    # The wait has run out: not_before is in the past but still set.
    await conn.execute(
        "update public.etl_jobs set not_before = now() - interval '1 second' where id = $1",
        deferred["id"],
    )
    job = await claim_next_job(conn, claimed_by="worker-a")
    assert job["id"] == deferred["id"]
    row = await conn.fetchrow(
        "select wait_reason, not_before, defer_count from public.etl_jobs where id = $1", job["id"]
    )
    assert row["wait_reason"] is None and row["not_before"] is None
    assert row["defer_count"] == 1


async def test_defer_by_a_non_owner_is_a_noop(db):
    conn = db["conn"]
    job = await claim_next_job(conn, claimed_by="worker-a")
    row = await defer_job(
        conn,
        job_id=job["id"],
        claimed_by="worker-b",
        expected_status="claimed",
        reason="not_codable",
    )
    assert row is None


async def test_unknown_reason_is_rejected(db):
    conn = db["conn"]
    with pytest.raises(ValueError):
        await _defer_first(conn, reason="bogus")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "update public.etl_jobs set wait_reason = 'bogus' where id = $1", db["job_ids"][1]
        )


async def test_wait_reason_is_only_valid_on_a_pending_job(db):
    conn = db["conn"]
    job = await claim_next_job(conn, claimed_by="worker-a")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(
            "update public.etl_jobs set wait_reason = 'not_codable' where id = $1", job["id"]
        )


async def _status_as(pg_cluster, db, *, jwt_user, owner_index: int):
    """The upload-status query (AA-63 adds wait_reason) on an RLS-scoped connection."""
    conn = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user="authenticated", database=db["dbname"]
    )
    try:
        async with conn.transaction():
            await conn.execute(
                "select set_config('request.jwt.claim.sub', $1, true)", str(jwt_user)
            )
            return await etl_jobs.get_job_status_for_bronze_file(
                conn,
                user_id=str(db["users"][owner_index]),
                bronze_file_id=str(db["bronze_ids"][owner_index]),
            )
    finally:
        await conn.close()


async def test_the_owner_reads_their_wait_reason_through_the_status_query(pg_cluster, db):
    await _defer_first(db["conn"])
    row = await _status_as(pg_cluster, db, jwt_user=db["users"][0], owner_index=0)
    assert row is not None and "wait_reason" in row.keys()
    assert row["wait_reason"] == "local_not_ready"


async def test_another_user_cannot_read_a_wait_reason(pg_cluster, db):
    await _defer_first(db["conn"])
    row = await _status_as(pg_cluster, db, jwt_user=db["users"][1], owner_index=0)
    assert row is None
