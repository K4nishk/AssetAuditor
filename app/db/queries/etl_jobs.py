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

# Manual-entry forms (KCH-55 / AA-20) parse synchronously inside the API
# request itself — there is no async extraction step for the worker to
# claim, so this lands the job directly at 'needs_user' (skipping
# pending/claimed/parsing) for the same parse-confirm screen (AA-17) every
# other upload uses.
_INSERT_NEEDS_USER_SQL = """
    insert into public.etl_jobs (user_id, bronze_file_id, status)
    values ($1, $2, 'needs_user')
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

_GET_JOB_SQL = """
    select id, user_id, bronze_file_id, status, claimed_by, claimed_at,
           error, created_at, updated_at
    from public.etl_jobs
    where user_id = $1 and id = $2
"""

# Only the parse-confirm screen's own transition (AA-17): a job awaiting
# confirmation moves to 'done' once the user confirms. Every other status
# change (pending -> claimed -> parsing -> needs_user/failed) is the worker's
# service_role-scoped `worker.queue.release_job`, not this RLS-scoped one.
_MARK_DONE_SQL = """
    update public.etl_jobs
    set status = 'done'
    where user_id = $1 and id = $2 and status = 'needs_user'
    returning id, user_id, bronze_file_id, status, claimed_by, claimed_at,
              error, created_at, updated_at
"""


async def enqueue_job(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record:
    return await conn.fetchrow(_ENQUEUE_SQL, user_id, bronze_file_id)


async def insert_needs_user_job(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record:
    return await conn.fetchrow(_INSERT_NEEDS_USER_SQL, user_id, bronze_file_id)


async def get_job_status_for_bronze_file(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_STATUS_BY_BRONZE_FILE_SQL, user_id, bronze_file_id)


async def get_job(conn: asyncpg.Connection, *, user_id: str, job_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_JOB_SQL, user_id, job_id)


async def mark_job_done(
    conn: asyncpg.Connection, *, user_id: str, job_id: str
) -> asyncpg.Record | None:
    """Transition `needs_user` -> `done`; `None` if the job doesn't exist,
    isn't this user's, or isn't currently awaiting confirmation."""
    return await conn.fetchrow(_MARK_DONE_SQL, user_id, job_id)
