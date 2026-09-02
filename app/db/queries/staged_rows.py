"""`staged_rows` reads/edits for the parse-confirm screen (KCH-52 / AA-17).

Every query here runs on an RLS-scoped connection (`app.db.pool.rls_connection`),
same convention as `app.db.queries.bronze_files`/`etl_jobs` — Postgres's own
RLS policy (migration 0001) is what actually enforces the tenant boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import asyncpg

from app.domain.staged_rows import encode_payload

_LIST_SQL = """
    select id, user_id, job_id, entity, payload, confidence, method,
           confirmed_at, deactivated_at, created_at
    from public.staged_rows
    where user_id = $1 and job_id = $2 and deactivated_at is null
    order by created_at
"""

_LIST_UNCONFIRMED_SQL = """
    select id, user_id, job_id, entity, payload, confidence, method,
           confirmed_at, deactivated_at, created_at
    from public.staged_rows
    where user_id = $1 and job_id = $2
        and deactivated_at is null and confirmed_at is null
    order by created_at
"""

# Editing is only ever allowed pre-confirm: a confirmed row has already been
# written to silver (app.db.queries.silver), so mutating its payload here
# afterward would silently desync it from what silver actually holds.
_UPDATE_PAYLOAD_SQL = """
    update public.staged_rows
    set payload = $4::jsonb, confidence = 1.0, method = 'manual_correction'
    where user_id = $1 and job_id = $2 and id = $3
        and deactivated_at is null and confirmed_at is null
    returning id, user_id, job_id, entity, payload, confidence, method,
              confirmed_at, deactivated_at, created_at
"""

_MARK_CONFIRMED_SQL = """
    update public.staged_rows
    set confirmed_at = now()
    where user_id = $1 and job_id = $2 and id = any($3::uuid[])
"""

_FIND_RUN_ID_SQL = """
    select run_id
    from public.lineage_events
    where user_id = $1 and job_id = $2
    order by occurred_at asc
    limit 1
"""

_INSERT_DRAFT_SQL = """
    insert into public.staged_rows (user_id, job_id, entity, payload, confidence, method)
    values ($1, $2, $3, $4::jsonb, $5, $6)
    returning id, user_id, job_id, entity, payload, confidence, method,
              confirmed_at, deactivated_at, created_at
"""


async def list_rows_for_job(
    conn: asyncpg.Connection, *, user_id: str, job_id: str, unconfirmed_only: bool = False
) -> list[asyncpg.Record]:
    sql = _LIST_UNCONFIRMED_SQL if unconfirmed_only else _LIST_SQL
    return await conn.fetch(sql, user_id, job_id)


async def update_row_payload(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    job_id: str,
    row_id: str,
    payload: dict[str, Any],
) -> asyncpg.Record | None:
    """Apply an inline edit, logged as `method='manual_correction'` per
    docs/vault/30-architecture/ETL-Pipeline.md. Returns `None` if the row
    doesn't exist, isn't this user's/job's, or is already confirmed/deactivated."""
    return await conn.fetchrow(
        _UPDATE_PAYLOAD_SQL, user_id, job_id, row_id, encode_payload(payload)
    )


async def mark_confirmed(
    conn: asyncpg.Connection, *, user_id: str, job_id: str, row_ids: Sequence[str]
) -> None:
    if not row_ids:
        return
    await conn.execute(_MARK_CONFIRMED_SQL, user_id, job_id, list(row_ids))


async def find_run_id_for_job(
    conn: asyncpg.Connection, *, user_id: str, job_id: str
) -> str | None:
    """The earliest `lineage_events` row for this job is its extraction START —
    the confirm step (worker/lineage.py) shares that same `run_id` so AA-23's
    drill-down can walk one run end to end. `None` if no extraction ever
    emitted lineage for this job (e.g. rows staged directly, as in tests)."""
    row = await conn.fetchrow(_FIND_RUN_ID_SQL, user_id, job_id)
    return str(row["run_id"]) if row is not None else None


async def insert_draft(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    job_id: str,
    entity: str,
    payload: dict[str, Any],
    confidence: float | None,
    method: str,
) -> asyncpg.Record:
    """Insert one `worker.adapters.base.StagedRowDraft`-shaped row. Not yet
    called from the worker job loop (extraction dispatch isn't wired into
    `worker/main.py`'s `job_poll_loop` — that wiring remains a gap, see
    CONTRACT_OUT.md); provided so a future wiring issue and this issue's own
    tests share one insert path instead of hand-building SQL twice."""
    return await conn.fetchrow(
        _INSERT_DRAFT_SQL,
        user_id,
        job_id,
        entity,
        encode_payload(payload),
        confidence,
        method,
    )
