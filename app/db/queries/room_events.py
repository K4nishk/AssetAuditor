"""`room_events` reads/writes for the rooms screen (KCH-44 / AA-9).

Runs on an RLS-scoped connection (`app.db.pool.rls_connection`), same
convention as `app.db.queries.users_profile`. Derived `kind='contribution'`
rows are owned by `worker.gold.rebuild_gold` (AA-18, via
`app.db.queries.gold.replace_derived_room_events`) — this module only reads
the full ledger and writes the one row kind the rooms screen originates
itself: `cra_override`, the reconciliation entry
`app.domain.rooms.engine.compute_rooms` already knows how to fold into the
ledger and explain the delta for.
"""

from __future__ import annotations

from decimal import Decimal

import asyncpg

_LIST_SQL = """
    select id, account_type, year, kind, amount, source_ref, created_at
    from public.room_events
    where user_id = $1 and deactivated_at is null
    order by year, created_at
"""

_INSERT_OVERRIDE_SQL = """
    insert into public.room_events (user_id, account_type, year, kind, amount)
    values ($1, $2, $3, 'cra_override', $4)
    returning id, account_type, year, kind, amount, source_ref, created_at
"""


async def list_room_events(conn: asyncpg.Connection, *, user_id: str) -> list[asyncpg.Record]:
    return await conn.fetch(_LIST_SQL, user_id)


async def insert_cra_override(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    account_type: str,
    year: int,
    amount: Decimal,
) -> asyncpg.Record:
    return await conn.fetchrow(_INSERT_OVERRIDE_SQL, user_id, account_type, year, amount)
