"""Live proof that the worker heartbeat lands in `worker_heartbeat` (KCH-39 / AA-4).

Mirrors tests/db/test_migration_0001_rls.py's approach: a real (ephemeral,
local) Postgres, not a mock, applying migration 0001 as-is. The worker
connects as `service_role` — approximated here by the cluster's superuser,
since RLS bypass for service_role is a Supabase-managed grant, not something
this migration defines (see 0001_init.sql's comments on `worker_heartbeat`).
Skips cleanly wherever Postgres tooling is unavailable, per CLAUDE.md.
"""

from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from worker.main import send_heartbeat

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    conn = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await conn.execute(MIGRATION_SQL_LOCAL)
        yield conn
    finally:
        await conn.close()


async def test_send_heartbeat_inserts_the_single_row(seeded_db):
    await send_heartbeat(seeded_db, status="online")

    row = await seeded_db.fetchrow("select id, status, last_beat_at from public.worker_heartbeat")
    assert row["id"] == 1
    assert row["status"] == "online"
    assert row["last_beat_at"] is not None


async def test_send_heartbeat_upserts_rather_than_duplicating(seeded_db):
    await send_heartbeat(seeded_db, status="online")
    first = await seeded_db.fetchrow("select last_beat_at from public.worker_heartbeat")

    await send_heartbeat(seeded_db, status="online")

    rows = await seeded_db.fetch("select id, last_beat_at from public.worker_heartbeat")
    assert len(rows) == 1
    assert rows[0]["last_beat_at"] >= first["last_beat_at"]
