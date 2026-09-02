"""Nightly retention sweeper (KCH-54 / AA-19).

docs/vault/20-domain/Data-Retention-and-Privacy.md's retention table:
bronze raw uploads get a 14-day TTL, DB-persisted job logs get 4 days. This
module runs on the worker's `WORKER_DATABASE_URL` connection (service_role,
bypasses RLS by design — same convention as `worker/queue.py`/`worker/lineage.py`)
since one sweep runs across every user's rows, not just one.

Two sweeps, kept distinct because they touch different data:

- `sweep_bronze_files` deletes the Blob bytes, marks `bronze_files.purged_at`,
  and emits a `retention_purge` START/COMPLETE lineage pair per file — the
  "purge tombstone" AA-23's drill-down panel falls back to once a source file
  is gone. A file whose blob delete fails is left unpurged so the next
  night's sweep retries it rather than losing the tombstone silently.
  The START event and a `purge_run_id`/`purge_started_at` outbox marker are
  persisted *before* Blob delete is ever called, so a DB failure after a
  successful delete (but before `purged_at` is set) still leaves the row
  retryable next sweep without losing the record that the delete happened —
  see `sweep_bronze_files`'s docstring for the two-phase commit shape.
- `sweep_job_logs` only clears `etl_jobs.error` on terminal (done/failed)
  jobs older than 4 days — it does NOT delete the `etl_jobs` row itself.
  `staged_rows` and `lineage_events` both FK to `etl_jobs` (the former
  `on delete cascade`), so hard-deleting the row would silently destroy
  confirmed staged_rows data and orphan lineage's job reference. `error` is
  the actual "log" content (an exception message); the row itself is job
  metadata/provenance, not a log, and is kept.

`run_retention_sweep` only records `retention_sweeper_last_success_timestamp`
once both sweeps finish without raising, AND no individual bronze blob delete
failed — mvp.md's AA-19 spec says to "treat sweeper-stale as a privacy
incident", so a partially-failed run (whether it raised outright or just left
some files unpurged) must NOT refresh the metric and mask that a full run is
overdue.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from app.uploads.blob import BlobDeleteError, BlobStorage, get_blob_storage
from worker import metrics
from worker.lineage import emit_lineage_event, new_run_id

logger = logging.getLogger("worker.retention")

BRONZE_RETENTION = timedelta(days=14)
JOB_LOG_RETENTION = timedelta(days=4)

_SELECT_BRONZE_PURGE_CANDIDATES_SQL = """
    select id, user_id, sha256, institution, blob_url, purge_run_id
    from public.bronze_files
    where purged_at is null and created_at < $1
    order by created_at
"""

# Outbox marker + `retention_purge` run id, persisted before Blob delete is
# ever called. A row that already carries one (a retry after a prior sweep
# died between delete and finalize) reuses it instead of re-emitting START.
_MARK_BRONZE_PURGE_PENDING_SQL = """
    update public.bronze_files
    set purge_run_id = $2, purge_started_at = $3
    where id = $1
"""

# blob_url can't go to null (`not null` in migration 0001) — empty string is
# the tombstone sentinel for "no bytes behind this row anymore".
_MARK_BRONZE_PURGED_SQL = """
    update public.bronze_files
    set purged_at = $2, blob_url = '', purge_run_id = null, purge_started_at = null
    where id = $1
"""

_SCRUB_JOB_LOGS_SQL = """
    update public.etl_jobs
    set error = null
    where status in ('done', 'failed')
        and updated_at < $1
        and error is not null
    returning id
"""


@dataclass(frozen=True)
class BronzeSweepResult:
    purged: int
    failed: int


async def sweep_bronze_files(
    conn: asyncpg.Connection, *, blob: BlobStorage, now: datetime
) -> BronzeSweepResult:
    """Purge `bronze_files` rows past `BRONZE_RETENTION`.

    Two-phase per row so a crash never leaves a deleted blob with no
    provenance trail:

    1. Persist a `purge_run_id`/`purge_started_at` outbox marker and emit the
       `retention_purge` START lineage event, committed *before* Blob delete
       is called. A row that already has `purge_run_id` set (a retry after a
       prior sweep died between step 2 and step 3) skips straight to step 2
       and reuses it rather than emitting a second START.
    2. Call `blob.delete`. A row whose delete fails is left pending (not
       purged, marker still set) and counted in `failed` rather than raised
       immediately, so the rest of the candidates still get attempted this
       run; `run_retention_sweep` uses `failed` to decide whether the sweep
       as a whole may be recorded as successful.
    3. Only once delete succeeds: mark `purged_at`, clear the outbox marker,
       and emit COMPLETE, all in one transaction.

    If a DB failure lands between step 2 and step 3, the row keeps its
    `purge_run_id` and stays unpurged, so the next sweep retries the delete
    (Blob delete-by-url is treated as idempotent — see `app.uploads.blob`'s
    module docstring on that being unverified against a live store) and then
    finalizes with the same run id, instead of losing the START event or
    silently re-purging under a fresh, disconnected run.
    """
    cutoff = now - BRONZE_RETENTION
    candidates = await conn.fetch(_SELECT_BRONZE_PURGE_CANDIDATES_SQL, cutoff)

    purged = 0
    failed = 0
    for row in candidates:
        facets = {
            "bronze_file_id": str(row["id"]),
            "sha256": row["sha256"],
            "institution": row["institution"],
            "reason": "14_day_retention_ttl",
        }

        run_id = row["purge_run_id"]
        if run_id is None:
            run_id = new_run_id()
            async with conn.transaction():
                await conn.execute(_MARK_BRONZE_PURGE_PENDING_SQL, row["id"], run_id, now)
                await emit_lineage_event(
                    conn,
                    user_id=str(row["user_id"]),
                    run_id=run_id,
                    step="retention_purge",
                    event_type="START",
                    facets=facets,
                )
        else:
            run_id = str(run_id)

        try:
            blob.delete(row["blob_url"])
        except BlobDeleteError:
            logger.exception(
                "retention sweep: failed to delete blob for bronze_file %s; "
                "leaving unpurged for the next sweep",
                row["id"],
            )
            failed += 1
            continue

        async with conn.transaction():
            await conn.execute(_MARK_BRONZE_PURGED_SQL, row["id"], now)
            await emit_lineage_event(
                conn,
                user_id=str(row["user_id"]),
                run_id=run_id,
                step="retention_purge",
                event_type="COMPLETE",
                facets=facets,
            )
        purged += 1

    return BronzeSweepResult(purged=purged, failed=failed)


async def sweep_job_logs(conn: asyncpg.Connection, *, now: datetime) -> int:
    """Scrub `etl_jobs.error` on terminal jobs past `JOB_LOG_RETENTION`. Returns count scrubbed."""
    cutoff = now - JOB_LOG_RETENTION
    rows = await conn.fetch(_SCRUB_JOB_LOGS_SQL, cutoff)
    return len(rows)


@dataclass(frozen=True)
class RetentionSweepResult:
    bronze_purged: int
    job_logs_scrubbed: int
    swept_at: datetime


async def run_retention_sweep(
    conn: asyncpg.Connection, *, blob: BlobStorage, now: datetime | None = None
) -> RetentionSweepResult:
    """Run both sweeps and, only on full success, record the success metric."""
    swept_at = now if now is not None else datetime.now(UTC)
    bronze = await sweep_bronze_files(conn, blob=blob, now=swept_at)
    job_logs_scrubbed = await sweep_job_logs(conn, now=swept_at)
    if bronze.failed == 0:
        metrics.record_sweeper_success(when=swept_at.timestamp())
    return RetentionSweepResult(
        bronze_purged=bronze.purged,
        job_logs_scrubbed=job_logs_scrubbed,
        swept_at=swept_at,
    )


async def main() -> None:
    """Standalone entrypoint (`python -m worker.retention`) for a one-off manual
    run — the primary schedule is `worker.main`'s `retention_sweep_loop`, which
    keeps the Prometheus gauge alive across sweeps on the long-lived worker box."""
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["WORKER_DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        result = await run_retention_sweep(conn, blob=get_blob_storage())
        logger.info(
            "retention sweep complete: bronze_purged=%s job_logs_scrubbed=%s",
            result.bronze_purged,
            result.job_logs_scrubbed,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
