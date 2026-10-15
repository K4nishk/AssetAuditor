"""`etl_jobs` claim primitive for the worker (AA-11).

Runs on the worker's `WORKER_DATABASE_URL` connection (service_role, bypasses
RLS by design — ADR v1.1.0 / AA-4) since one worker process claims jobs
across every user, not just one. `FOR UPDATE SKIP LOCKED` is what lets a
future second worker process run the same query concurrently without either
one blocking on, or double-claiming, a row the other already grabbed.
"""

from __future__ import annotations

import asyncpg

from worker import metrics

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
          and (not_before is null or not_before <= now())
        order by created_at
        for update skip locked
        limit 1
    )
    update public.etl_jobs
    set status = 'claimed', claimed_by = $1, claimed_at = now(),
        wait_reason = null, not_before = null
    from next_job
    where etl_jobs.id = next_job.id
    returning etl_jobs.id, etl_jobs.user_id, etl_jobs.bronze_file_id,
              etl_jobs.status, etl_jobs.claimed_by, etl_jobs.claimed_at,
              etl_jobs.created_at
"""

_RELEASE_JOB_SQL = """
    update public.etl_jobs
    set status = $4, error = $5
    where id = $1 and claimed_by = $2 and status = $3
    returning id, status, error,
              extract(epoch from (now() - claimed_at))::float8 as duration_seconds,
              (select institution from public.bronze_files
               where id = etl_jobs.bronze_file_id) as institution
"""

# Defer (AA-63): lock the owned row first so the backoff step is read and
# applied in one transaction, then return it to 'pending' with its reason.
_LOCK_OWNED_JOB_SQL = """
    select defer_count
    from public.etl_jobs
    where id = $1 and claimed_by = $2 and status = $3
    for update
"""

_DEFER_JOB_SQL = """
    update public.etl_jobs
    set status = 'pending', wait_reason = $4, claimed_by = null, claimed_at = null,
        not_before = now() + make_interval(mins => $5),
        defer_count = defer_count + 1
    where id = $1 and claimed_by = $2 and status = $3
    returning id, status, wait_reason, claimed_by, defer_count,
              extract(epoch from (not_before - now()))::float8 as wait_seconds
"""

# Early wake (AA-63): AA-67 calls this when the local model turns ready, so
# waiting jobs don't sit out the rest of their backoff.
_EARLY_WAKE_SQL = """
    with woken as (
        update public.etl_jobs
        set not_before = null
        where status = 'pending' and not_before > now()
        returning 1
    )
    select count(*) from woken
"""

# Cross-user by design, same as the claim query above — feeds the
# `etl_jobs_queued` gauge (AA-34) that pairs with `worker_heartbeat_timestamp`
# for the stale-while-queued alert (docs/vault/30-architecture/Observability.md).
_COUNT_QUEUED_JOBS_SQL = "select count(*) from public.etl_jobs where status = 'pending'"

_VALID_STATUSES = frozenset({"pending", "claimed", "parsing", "needs_user", "done", "failed"})

# Must match migration 0005's check constraint and
# app.domain.worker_status.WAIT_REASON_MESSAGES (tests keep all three in step).
WAIT_REASONS = frozenset(
    {
        "local_not_ready",
        "local_unavailable",
        "not_codable",
        "check_failed",
        "egress_unavailable",
        "coded_result_invalid",
    }
)

# Owner decision R4.1: 1, 2, 4, 8, 16, 32 minutes, then every 60.
BACKOFF_MINUTES = (1, 2, 4, 8, 16, 32, 60)

_DEFERRABLE_STATUSES = frozenset({"claimed", "parsing"})

# Which `expected_status` a job may move on from, and what it may become.
# Keeps `release_job` from being used to jump a job backward (e.g. "done" ->
# "claimed") or sideways into "pending", which only `defer_job` may set.
_ALLOWED_TRANSITIONS = {
    "claimed": frozenset({"parsing", "needs_user", "done", "failed"}),
    "parsing": frozenset({"needs_user", "done", "failed"}),
    "needs_user": frozenset({"parsing", "done", "failed"}),
}


async def claim_next_job(conn: asyncpg.Connection, *, claimed_by: str) -> asyncpg.Record | None:
    """Atomically claim the oldest pending job, or return `None` if the queue is empty."""
    return await conn.fetchrow(_CLAIM_NEXT_JOB_SQL, claimed_by)


async def count_queued_jobs(conn: asyncpg.Connection) -> int:
    """Count `etl_jobs` still waiting to be claimed, for the `etl_jobs_queued` gauge."""
    return await conn.fetchval(_COUNT_QUEUED_JOBS_SQL)


async def release_job(
    conn: asyncpg.Connection,
    *,
    job_id: str,
    claimed_by: str,
    expected_status: str,
    status: str,
    error: str | None = None,
) -> asyncpg.Record | None:
    """Transition a claimed job to its next status (parsing/needs_user/done/failed).

    `claimed_by` and `expected_status` both bind into the `UPDATE`'s `WHERE`
    clause so only the worker that currently owns the job — from the state it
    believes the job is still in — can move it; a worker acting on a stale
    claim (another worker already advanced or re-claimed the row) gets `None`
    back instead of silently overwriting someone else's transition. The
    status pair must also be a real forward transition (`_ALLOWED_TRANSITIONS`)
    so this can't be used to move a job backward or into `pending`, which only
    `defer_job` may set.

    Extraction itself (parsing bytes, writing staged rows) is AA-14/15/16's
    job; this only records the outcome those issues arrive at.

    Every successful transition feeds `worker.metrics.etl_jobs_total`; only a
    terminal one (done/failed) also feeds `etl_job_duration_seconds` (AA-27) —
    a claimed->parsing or parsing->needs_user hop isn't the job finishing, so
    counting its elapsed-since-claimed time would understate real job
    durations and skew the histogram. Instrumenting here, rather than at
    whatever future call site claims/dispatches jobs, means every caller of
    this primitive is counted without having to remember to do it itself.
    """
    if expected_status not in _VALID_STATUSES:
        raise ValueError(f"invalid etl_jobs status: {expected_status!r}")
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid etl_jobs status: {status!r}")
    if status not in _ALLOWED_TRANSITIONS.get(expected_status, frozenset()):
        raise ValueError(f"invalid etl_jobs transition: {expected_status!r} -> {status!r}")
    row = await conn.fetchrow(_RELEASE_JOB_SQL, job_id, claimed_by, expected_status, status, error)
    if row is not None:
        metrics.record_etl_job_outcome(status=row["status"], institution=row.get("institution"))
        if row["status"] in {"done", "failed"}:
            duration_seconds = row.get("duration_seconds")
            if duration_seconds is not None:
                metrics.record_etl_job_duration(duration_seconds)
    return row


def backoff_minutes(defer_count: int) -> int:
    """Minutes to wait after the `defer_count`-th deferral (0-based): 1, 2, 4 ... 32, then 60."""
    if defer_count < 0:
        raise ValueError(f"defer_count must be >= 0, got {defer_count}")
    return BACKOFF_MINUTES[min(defer_count, len(BACKOFF_MINUTES) - 1)]


async def defer_job(
    conn: asyncpg.Connection,
    *,
    job_id: str,
    claimed_by: str,
    expected_status: str,
    reason: str,
) -> asyncpg.Record | None:
    """Return an owned job to `pending` with a wait reason and a backed-off `not_before`.

    Same guard as `release_job`: only the worker that owns the job, from the
    status it believes the job is in, can defer it; anyone else gets `None`.
    `claim_next_job` skips the job until `not_before` and clears the reason
    when it claims it again.
    """
    if reason not in WAIT_REASONS:
        raise ValueError(f"invalid etl_jobs wait reason: {reason!r}")
    if expected_status not in _DEFERRABLE_STATUSES:
        raise ValueError(f"cannot defer an etl_jobs row from status {expected_status!r}")
    async with conn.transaction():
        defer_count = await conn.fetchval(_LOCK_OWNED_JOB_SQL, job_id, claimed_by, expected_status)
        if defer_count is None:
            return None
        return await conn.fetchrow(
            _DEFER_JOB_SQL,
            job_id,
            claimed_by,
            expected_status,
            reason,
            backoff_minutes(defer_count),
        )


async def early_wake(conn: asyncpg.Connection) -> int:
    """Make every waiting job claimable now; returns how many were woken."""
    return await conn.fetchval(_EARLY_WAKE_SQL)
