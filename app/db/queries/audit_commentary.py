"""`audit_commentary` reads/writes (KCH-62 / AA-25).

`write_commentary` is worker-only — called from
`worker.commentary.generate_audit_commentary` on whichever connection its
caller passes (RLS-scoped or the worker's service_role, same convention
`app.db.queries.gold.write_gold_snapshot` documents), delete-then-reinsert
per `(user_id, snapshot_date)` so a regenerate replaces rather than
accumulates rows. `get_latest_commentary` is the API's read (RLS-scoped
connection, same convention as `app.db.queries.dashboard`).
"""

from __future__ import annotations

import json
from datetime import date

import asyncpg

_DELETE_SQL = """
    delete from public.audit_commentary where user_id = $1 and snapshot_date = $2
"""

_INSERT_SQL = """
    insert into public.audit_commentary
        (user_id, snapshot_date, observations, disclosure, model_backend, run_id)
    values ($1, $2, $3::jsonb, $4, $5, $6)
    returning id, user_id, snapshot_date, observations, disclosure, model_backend,
        run_id, created_at
"""

_LATEST_SQL = """
    select snapshot_date, observations, disclosure, model_backend, run_id, created_at
    from public.audit_commentary
    where user_id = $1 and deactivated_at is null
    order by snapshot_date desc
    limit 1
"""


async def write_commentary(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    snapshot_date: date,
    observations: list[str],
    disclosure: str,
    model_backend: str,
    run_id: str,
) -> asyncpg.Record:
    await conn.execute(_DELETE_SQL, user_id, snapshot_date)
    return await conn.fetchrow(
        _INSERT_SQL,
        user_id,
        snapshot_date,
        json.dumps(observations),
        disclosure,
        model_backend,
        run_id,
    )


async def get_latest_commentary(
    conn: asyncpg.Connection, *, user_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(_LATEST_SQL, user_id)


__all__ = ["write_commentary", "get_latest_commentary"]
