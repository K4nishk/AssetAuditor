"""`etl_jobs` claim primitive for the worker (AA-11).

Runs on the worker's `WORKER_DATABASE_URL` connection (service_role, bypasses
RLS by design — ADR v1.1.0 / AA-4) since one worker process claims jobs
across every user, not just one. `FOR UPDATE SKIP LOCKED` is what lets a
future second worker process run the same query concurrently without either
one blocking on, or double-claiming, a row the other already grabbed.
"""

from __future__ import annotations

import asyncpg

# The CTE, rather than `UPDATE ... WHERE id = (SELECT ... LIMIT 1)`, is the
# standard-library-recommended shape for this: `FOR UPDATE SKIP LOCKED` has to
# run on a plain SELECT (an UPDATE's own row lock isn't skippable the same
# way), and the CTE hands that already-locked row straight to the UPDATE
# without a second query that could race against another worker in between.
_CLAIM_NEXT_JOB_SQL = """
    with next_job as (
        select id
        from public.etl_jobs
        where status = 'pending'
        order by created_at
        for update skip locked
        limit 1
    )
    update public.etl_jobs
    set status = 'claimed', claimed_by = $1, claimed_at = now()
    from next_job
    where etl_jobs.id = next_job.id
    returning etl_jobs.id, etl_jobs.user_id, etl_jobs.bronze_file_id,
              etl_jobs.status, etl_jobs.claimed_by, etl_jobs.claimed_at,
              etl_jobs.created_at
"""

_RELEASE_JOB_SQL = """
    update public.etl_jobs
    set status = $2, error = $3
    where id = $1
    returning id, status, error
"""

_VALID_STATUSES = frozenset({"pending", "claimed", "parsing", "needs_user", "done", "failed"})


async def claim_next_job(conn: asyncpg.Connection, *, claimed_by: str) -> asyncpg.Record | None:
    """Atomically claim the oldest pending job, or return `None` if the queue is empty."""
    return await conn.fetchrow(_CLAIM_NEXT_JOB_SQL, claimed_by)


async def release_job(
    conn: asyncpg.Connection, *, job_id: str, status: str, error: str | None = None
) -> asyncpg.Record | None:
    """Transition a claimed job to its next status (parsing/needs_user/done/failed).

    Extraction itself (parsing bytes, writing staged rows) is AA-14/15/16's
    job; this only records the outcome those issues arrive at.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid etl_jobs status: {status!r}")
    return await conn.fetchrow(_RELEASE_JOB_SQL, job_id, status, error)
