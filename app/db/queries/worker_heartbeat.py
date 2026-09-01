"""Read access to the shared `worker_heartbeat` row (AA-34).

Unlike the rest of `app.db.queries`, this runs fine on the RLS-scoped
`authenticated` connection (`app.db.pool.rls_connection`) — migration 0001
grants that role a read-only `using (true)` policy on `worker_heartbeat`
specifically so routes like `GET /api/uploads/{id}/status` can check
liveness without a service_role connection.
"""

from __future__ import annotations

import asyncpg

_GET_LATEST_HEARTBEAT_SQL = (
    "select id, last_beat_at, status from public.worker_heartbeat where id = 1"
)


async def get_latest_heartbeat(conn: asyncpg.Connection) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_LATEST_HEARTBEAT_SQL)
