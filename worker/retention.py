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
- `sweep_job_logs` only clears `etl_jobs.error` on terminal (done/failed)
  jobs older than 4 days — it does NOT delete the `etl_jobs` row itself.
  `staged_rows` and `lineage_events` both FK to `etl_jobs` (the former
  `on delete cascade`), so hard-deleting the row would silently destroy
  confirmed staged_rows data and orphan lineage's job reference. `error` is
  the actual "log" content (an exception message); the row itself is job
  metadata/provenance, not a log, and is kept.

`run_retention_sweep` only records `retention_sweeper_last_success_timestamp`
once both sweeps finish without raising — mvp.md's AA-19 spec says to
"treat sweeper-stale as a privacy incident", so a partially-failed run must
NOT refresh the metric and mask that a full run is overdue.
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
from worker.lineage import LineageEmitter

logger = logging.getLogger("worker.retention")

BRONZE_RETENTION = timedelta(days=14)
JOB_LOG_RETENTION = timedelta(days=4)

_SELECT_BRONZE_PURGE_CANDIDATES_SQL = """
    select id, user_id, sha256, institution, blob_url
    from public.bronze_files
    where purged_at is null and created_at < $1
    order by created_at
"""

# blob_url can't go to null (`not null` in migration 0001) — empty string is
# the tombstone sentinel for "no bytes behind this row anymore".
_MARK_BRONZE_PURGED_SQL = """
    update public.bronze_files
    set purged_at = $2, blob_url = ''
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


async def sweep_bronze_files(
    conn: asyncpg.Connection, *, blob: BlobStorage, now: datetime
) -> int:
    """Purge `bronze_files` rows past `BRONZE_RETENTION`. Returns count purged."""
    cutoff = now - BRONZE_RETENTION
    candidates = await conn.fetch(_SELECT_BRONZE_PURGE_CANDIDATES_SQL, cutoff)

    purged = 0
    for row in candidates:
        try:
            blob.delete(row["blob_url"])
        except BlobDeleteError:
            logger.exception(
                "retention sweep: failed to delete blob for bronze_file %s; "
                "leaving unpurged for the next sweep",
                row["id"],
            )
            continue

        facets = {
            "bronze_file_id": str(row["id"]),
            "sha256": row["sha256"],
            "institution": row["institution"],
            "reason": "14_day_retention_ttl",
        }
        async with conn.transaction():
            await conn.execute(_MARK_BRONZE_PURGED_SQL, row["id"], now)
            emitter = LineageEmitter(conn, user_id=str(row["user_id"]))
            await emitter.start("retention_purge", facets=facets)
            await emitter.complete("retention_purge", facets=facets)
        purged += 1

    return purged


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
    bronze_purged = await sweep_bronze_files(conn, blob=blob, now=swept_at)
    job_logs_scrubbed = await sweep_job_logs(conn, now=swept_at)
    metrics.record_sweeper_success(when=swept_at.timestamp())
    return RetentionSweepResult(
        bronze_purged=bronze_purged,
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
