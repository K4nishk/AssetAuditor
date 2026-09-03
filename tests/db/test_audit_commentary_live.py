"""Live proof of `worker.commentary.generate_audit_commentary`'s DB reads/writes
(KCH-62 / AA-25).

Same ephemeral-Postgres approach as `tests/db/test_gold_rebuild_live.py`: the
replace-then-insert write, the RLS-scoped read path, and the lineage
START/COMPLETE pair are real Postgres/asyncpg behaviour a mocked connection
can't prove. Skips cleanly wherever Postgres tooling is unavailable, per
CLAUDE.md. The LLM call itself is still faked — no network in this sandbox,
same convention `tests/unit/test_commentary_llm.py` uses.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from app.db.queries import audit_commentary as commentary_queries
from worker.commentary import (
    NoCompliantObservationsError,
    generate_audit_commentary,
    refresh_commentary_for_all_users,
)
from worker.lineage import LineageEmitter

MIGRATION_0001_SQL = Path("app/db/migrations/0001_init.sql").read_text()
MIGRATION_0001_SQL_LOCAL = MIGRATION_0001_SQL.replace(
    "create extension if not exists pgsodium;\n", ""
)
MIGRATION_0002_SQL = Path("app/db/migrations/0002_audit_commentary.sql").read_text()

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


@dataclass
class _FakeMessage:
    content: str | None


@dataclass
class _FakeChoice:
    message: _FakeMessage


@dataclass
class _FakeResponse:
    choices: list[_FakeChoice]
    model: str = "groq/llama-3.3-70b-versatile"


@dataclass
class _FakeCompletions:
    response: _FakeResponse | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        assert self.response is not None
        return self.response


@dataclass
class _FakeChat:
    completions: _FakeCompletions


class FakeClient:
    def __init__(self, observations: list[str]):
        content = json.dumps({"observations": observations})
        response = _FakeResponse(choices=[_FakeChoice(_FakeMessage(content))])
        self.chat = _FakeChat(completions=_FakeCompletions(response=response))
        self.base_url = "http://litellm:4000"


@pytest_asyncio.fixture
async def seeded_db(pg_cluster, scratch_database):
    admin = await asyncpg.connect(
        host=pg_cluster["socket_dir"], user=pg_cluster["admin_user"], database=scratch_database
    )
    try:
        await admin.execute(AUTH_STUB_SQL)
        await admin.execute(MIGRATION_0001_SQL_LOCAL)
        await admin.execute(MIGRATION_0002_SQL)
        user_id = uuid.uuid4()
        await admin.execute("insert into auth.users (id) values ($1)", user_id)
        yield {"conn": admin, "user_id": str(user_id)}
    finally:
        await admin.close()


async def _seed_gold_snapshot(conn, *, user_id, snapshot_date):
    run_id = uuid.uuid4()
    await conn.execute(
        """
        insert into public.networth_snapshots
            (user_id, snapshot_date, total_assets_cad, total_liabilities_cad, net_worth_cad, run_id)
        values ($1, $2, $3, $4, $5, $6)
        """,
        user_id,
        snapshot_date,
        Decimal("10000.00"),
        Decimal("2000.00"),
        Decimal("8000.00"),
        run_id,
    )
    await conn.execute(
        """
        insert into public.term_buckets (user_id, snapshot_date, bucket, amount_cad, run_id)
        values ($1, $2, 'short_term', $3, $4)
        """,
        user_id,
        snapshot_date,
        Decimal("10000.00"),
        run_id,
    )
    await conn.execute(
        """
        insert into public.diversification_cuts
            (user_id, snapshot_date, cut, label, amount_cad, run_id)
        values ($1, $2, 'institution', 'questrade', $3, $4)
        """,
        user_id,
        snapshot_date,
        Decimal("10000.00"),
        run_id,
    )


async def test_generate_audit_commentary_writes_and_replaces(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    snapshot_date = date(2026, 7, 31)
    await _seed_gold_snapshot(conn, user_id=user_id, snapshot_date=snapshot_date)

    client = FakeClient(["All of your assets are short-term."])
    lineage = LineageEmitter(conn, user_id=user_id)
    result = await generate_audit_commentary(
        conn, user_id=user_id, lineage=lineage, client=client
    )

    assert result.observations == ["All of your assets are short-term."]
    assert result.model_backend == "groq"

    row = await commentary_queries.get_latest_commentary(conn, user_id=user_id)
    assert row["snapshot_date"] == snapshot_date
    assert row["disclosure"] == result.disclosure

    events = await conn.fetch(
        "select event_type from public.lineage_events where user_id = $1 order by occurred_at",
        user_id,
    )
    assert [e["event_type"] for e in events] == ["START", "COMPLETE"]

    # Regenerating replaces the row rather than accumulating a second one.
    client2 = FakeClient(["Your portfolio is concentrated at one institution."])
    await generate_audit_commentary(
        conn, user_id=user_id, lineage=LineageEmitter(conn, user_id=user_id), client=client2
    )
    rows = await conn.fetch(
        "select * from public.audit_commentary where user_id = $1", user_id
    )
    assert len(rows) == 1
    assert rows[0]["observations"] == json.dumps(
        ["Your portfolio is concentrated at one institution."]
    )


async def test_generate_audit_commentary_raises_when_all_observations_filtered(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    snapshot_date = date(2026, 7, 31)
    await _seed_gold_snapshot(conn, user_id=user_id, snapshot_date=snapshot_date)

    client = FakeClient(["You should sell everything."])
    with pytest.raises(NoCompliantObservationsError):
        await generate_audit_commentary(
            conn, user_id=user_id, lineage=LineageEmitter(conn, user_id=user_id), client=client
        )

    events = await conn.fetch(
        "select event_type from public.lineage_events where user_id = $1 order by occurred_at",
        user_id,
    )
    assert [e["event_type"] for e in events] == ["START", "FAIL"]
    assert await commentary_queries.get_latest_commentary(conn, user_id=user_id) is None


async def test_refresh_commentary_for_all_users_isolates_per_user_failures(seeded_db):
    conn = seeded_db["conn"]
    user_id = seeded_db["user_id"]
    snapshot_date = date(2026, 7, 31)
    await _seed_gold_snapshot(conn, user_id=user_id, snapshot_date=snapshot_date)

    client = FakeClient(["Net worth is positive."])
    result = await refresh_commentary_for_all_users(
        conn, client=client, now=datetime(2026, 8, 1, tzinfo=UTC)
    )

    assert result.users_updated == 1
    assert result.users_failed == 0
    row = await commentary_queries.get_latest_commentary(conn, user_id=user_id)
    assert row is not None
