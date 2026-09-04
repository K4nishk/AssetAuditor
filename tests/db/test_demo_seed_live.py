"""Live proof of the demo-mode seed pipeline (KCH-69 / AA-32).

Same ephemeral-Postgres approach as tests/db/test_manual_entry_live.py and
tests/db/test_gold_rebuild_live.py: staging every `data/samples/` fixture
through the real natural-key silver resolver and then rebuilding gold from
it is genuine Postgres/constraint behaviour a mocked connection can't prove
(app.routes.demo's own module docstring — a call-count-accurate fake
connection for a 7-fixture pipeline plus `rebuild_gold` would be more brittle
than the thing it's proving). Skips cleanly wherever Postgres tooling is
unavailable, per CLAUDE.md.

Drives `app.routes.demo`'s private per-fixture/reset helpers directly rather
than going through `TestClient` — the route layer's own responsibility (the
`DEMO_USER_ID` gate) is already covered by tests/api/test_demo_routes.py
without needing a live DB at all.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries.account_lifecycle import purge_user_rows
from app.db.queries.users_profile import get_profile, upsert_profile
from app.domain.demo import ALEX_MOCK_PROFILE, DEMO_FIXTURES, DEMO_SNAPSHOT_DATE
from app.routes.demo import _SILVER_ENTITIES, _seed_one_fixture
from worker.gold import rebuild_gold
from worker.lineage import LineageEmitter

MIGRATION_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_SQL_LOCAL = MIGRATION_SQL.replace("create extension if not exists pgsodium;\n", "")
MIGRATION_0003_SQL = Path("app/db/migrations/0003_holdings_fee_drag.sql").read_text()

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


class FakeBlobStorage:
    """Records every call in memory — no real Vercel Blob in this sandbox,
    same pattern tests/unit/test_gold_export.py uses."""

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.deleted_prefixes: list[str] = []

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        self.puts.append((pathname, data, content_type))
        return f"https://blob.example/{pathname}"

    def delete(self, url: str) -> None:  # pragma: no cover - not exercised here
        pass

    def delete_prefix(self, prefix: str) -> int:
        self.deleted_prefixes.append(prefix)
        return 0


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_SQL_LOCAL)
        await admin.execute(MIGRATION_0003_SQL)

        user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", user_id)

        yield {"conn": admin, "user_id": str(user_id)}
    finally:
        await admin.close()


async def _seed_all_fixtures(conn, *, user_id: str, blob: FakeBlobStorage) -> dict[str, int]:
    silver_summary = dict.fromkeys(_SILVER_ENTITIES, 0)
    for fixture in DEMO_FIXTURES:
        summary = await _seed_one_fixture(conn, user_id=user_id, blob=blob, fixture=fixture)
        for entity, count in summary.items():
            silver_summary[entity] += count
    return silver_summary


async def test_seed_loads_every_demo_fixture_into_silver_and_rebuilds_gold(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    blob = FakeBlobStorage()

    silver_summary = await _seed_all_fixtures(conn, user_id=user_id, blob=blob)

    # Every fixture stages at least one account, and every institution's
    # bytes actually made it to "blob storage" once each.
    assert silver_summary["account"] == len(DEMO_FIXTURES)
    assert len(blob.puts) == len(DEMO_FIXTURES)

    await upsert_profile(conn, user_id=user_id, **ALEX_MOCK_PROFILE)
    profile = await get_profile(conn, user_id=user_id)
    assert profile["holdings_country"] == "CA"
    assert profile["risk_profile"] == "medium"

    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=DEMO_SNAPSHOT_DATE,
        lineage=LineageEmitter(conn, user_id=user_id),
        blob=blob,
    )

    assert result.totals.total_assets_cad > 0
    # FHSA contributions (wealthsimple.json) derive at least one room event.
    assert result.room_events_written >= 1

    snapshot = await conn.fetchrow(
        "select * from public.networth_snapshots where user_id = $1", user_id
    )
    assert snapshot["snapshot_date"] == date(2026, 7, 31)

    accounts = await conn.fetch(
        "select institution from public.accounts where user_id = $1", user_id
    )
    institutions = {row["institution"] for row in accounts}
    assert institutions == {f.adapter.INSTITUTION for f in DEMO_FIXTURES}


async def test_reseeding_after_a_purge_does_not_duplicate_silver_rows(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    blob = FakeBlobStorage()

    first = await _seed_all_fixtures(conn, user_id=user_id, blob=blob)

    await purge_user_rows(conn, user_id=user_id)
    remaining = await conn.fetch("select id from public.accounts where user_id = $1", user_id)
    assert remaining == []

    second = await _seed_all_fixtures(conn, user_id=user_id, blob=blob)

    assert second == first
    accounts = await conn.fetch("select id from public.accounts where user_id = $1", user_id)
    assert len(accounts) == len(DEMO_FIXTURES)
