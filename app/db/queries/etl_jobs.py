"""`etl_jobs` reads/writes for the API side of the upload path (AA-11).

These run on an RLS-scoped connection (`app.db.pool.rls_connection`), same as
`app.db.queries.bronze_files`. The worker-side claim query
(`FOR UPDATE SKIP LOCKED`, cross-user by design) lives in `worker/queue.py`
instead — it runs on the service_role connection that bypasses RLS.
"""

from __future__ import annotations

import asyncpg

_ENQUEUE_SQL = """
    insert into public.etl_jobs (user_id, bronze_file_id)
    values ($1, $2)
    returning id, user_id, bronze_file_id, status, claimed_by, claimed_at,
              error, created_at, updated_at
"""

_GET_STATUS_BY_BRONZE_FILE_SQL = """
    select id, user_id, bronze_file_id, status, claimed_by, claimed_at,
           error, created_at, updated_at
    from public.etl_jobs
    where user_id = $1 and bronze_file_id = $2
    order by created_at desc
    limit 1
"""


async def enqueue_job(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record:
    return await conn.fetchrow(_ENQUEUE_SQL, user_id, bronze_file_id)


async def get_job_status_for_bronze_file(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_STATUS_BY_BRONZE_FILE_SQL, user_id, bronze_file_id)
