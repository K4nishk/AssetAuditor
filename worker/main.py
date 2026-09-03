"""ETL worker entrypoint — home-lab GPU box (ADR v1.1.0).

AA-4 (deploy rails) wires the heartbeat loop below to prove the box can reach
Supabase from docker-compose: `bring-up.md`'s smoke test. AA-11 adds
`job_poll_loop`, claiming `etl_jobs` via `worker.queue.claim_next_job`
(`FOR UPDATE SKIP LOCKED`). AA-34 adds the `/metrics` endpoint (`worker.metrics`)
and the `etl_jobs_queued` gauge refresh below. All three extend `main()`'s
loop rather than replacing it.

`job_poll_loop` only claims and logs — it does not parse anything. Actual
extraction (adapters, pdfplumber/LLM tiers) is AA-14/15/16's job; claiming a
job here just proves the queue mechanism works end to end, leaving the row at
`status = 'claimed'` for those issues to advance further.

AA-19 adds `retention_sweep_loop`, running `worker.retention.run_retention_sweep`
once a day on this same long-lived process — the "nightly Action" mvp.md's
AA-19 describes. Keeping it in-process (rather than a separate scheduled job)
is what lets `retention_sweeper_last_success_timestamp` behave like the other
worker gauges: a normal in-memory Prometheus value scraped off `/metrics`,
per docs/vault/10-mental-models/Push-Dont-Scrape-Metrics.md ("the long-lived
ETL worker can still expose a normal /metrics scrape target").

AA-21 adds `price_refresh_loop`, same shape again, running
`worker.prices.refresh_prices` once a day — the "daily cron" mvp.md's AA-21
describes; `python -m worker.prices` (see that module's docstring) covers
the "on-demand refresh" half of the same issue.

AA-25 adds `commentary_loop`, same shape again, running
`worker.commentary.refresh_commentary_for_all_users` once a day so every
user's dashboard "observations" card (ADR v1.1.0 §3: LLM calls only ever
originate on the worker) stays current with their latest gold snapshot;
`python -m worker.commentary` covers the on-demand run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid

import asyncpg

from app.uploads.blob import BlobStorage, get_blob_storage
from worker import metrics
from worker.commentary import refresh_commentary_for_all_users
from worker.prices import PriceFetcher, YFinancePriceFetcher, refresh_prices
from worker.queue import claim_next_job, count_queued_jobs
from worker.retention import run_retention_sweep

HEARTBEAT_INTERVAL_SECONDS = 30
JOB_POLL_INTERVAL_SECONDS = 5
RETENTION_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60
PRICE_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60
COMMENTARY_REFRESH_INTERVAL_SECONDS = 24 * 60 * 60

logger = logging.getLogger("worker")


def _default_worker_id() -> str:
    return os.environ.get("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"

# Single-row upsert (id is fixed at 1 by the table's check constraint —
# app/db/migrations/0001_init.sql). Connects as service_role, which bypasses
# RLS by design; parameterized per CLAUDE.md hard rule #3.
_UPSERT_HEARTBEAT_SQL = """
    insert into public.worker_heartbeat (id, last_beat_at, status)
    values (1, now(), $1)
    on conflict (id) do update
        set last_beat_at = excluded.last_beat_at,
            status = excluded.status
"""


async def send_heartbeat(conn: asyncpg.Connection, status: str = "online") -> None:
    await conn.execute(_UPSERT_HEARTBEAT_SQL, status)
    metrics.record_heartbeat()


async def heartbeat_loop(conn: asyncpg.Connection, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await send_heartbeat(conn)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def job_poll_loop(
    conn: asyncpg.Connection, stop_event: asyncio.Event, *, worker_id: str
) -> None:
    while not stop_event.is_set():
        job = await claim_next_job(conn, claimed_by=worker_id)
        metrics.record_queue_depth(await count_queued_jobs(conn))
        if job is not None:
            logger.info(
                "claimed etl_job %s (bronze_file_id=%s) — extraction not yet implemented",
                job["id"],
                job["bronze_file_id"],
            )
            continue  # more pending jobs may be waiting; don't sleep between claims
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=JOB_POLL_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def retention_sweep_loop(
    conn: asyncpg.Connection, stop_event: asyncio.Event, *, blob: BlobStorage
) -> None:
    while not stop_event.is_set():
        try:
            result = await run_retention_sweep(conn, blob=blob)
            logger.info(
                "retention sweep complete: bronze_purged=%s job_logs_scrubbed=%s",
                result.bronze_purged,
                result.job_logs_scrubbed,
            )
        except Exception:
            # Deliberately broad: any failure here must NOT crash the whole
            # worker process (heartbeat/job-poll loops keep running), and
            # must NOT record a false success — mvp.md's AA-19 spec is to
            # treat a stale sweeper as a privacy incident, so the metric
            # staying stale on failure is the intended signal, not a bug.
            logger.exception("retention sweep failed; will retry next interval")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=RETENTION_SWEEP_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def price_refresh_loop(
    conn: asyncpg.Connection, stop_event: asyncio.Event, *, fetcher: PriceFetcher
) -> None:
    while not stop_event.is_set():
        try:
            result = await refresh_prices(conn, fetcher=fetcher)
            logger.info(
                "price refresh complete: prices_written=%s fx_written=%s symbols_failed=%s",
                result.prices_written,
                result.fx_written,
                result.symbols_failed,
            )
        except Exception:
            # Same deliberately-broad convention as retention_sweep_loop: a
            # failed refresh must not crash the whole worker process, and
            # must not record a false success — the gauge staying stale is
            # the intended signal that the next scheduled/on-demand run
            # still needs to happen.
            logger.exception("price refresh failed; will retry next interval")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=PRICE_REFRESH_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def commentary_loop(conn: asyncpg.Connection, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            result = await refresh_commentary_for_all_users(conn)
            logger.info(
                "audit commentary refresh complete: users_updated=%s users_failed=%s",
                result.users_updated,
                result.users_failed,
            )
        except Exception:
            # Same deliberately-broad convention as retention_sweep_loop/
            # price_refresh_loop: one bad cycle must not crash the whole
            # worker process, and per-user failures are already isolated and
            # counted inside refresh_commentary_for_all_users itself — this
            # only catches something breaking before/after that loop (e.g.
            # the initial `select distinct user_id` query).
            logger.exception("audit commentary refresh failed; will retry next interval")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=COMMENTARY_REFRESH_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["WORKER_DATABASE_URL"]
    worker_id = _default_worker_id()
    metrics_port = int(os.environ.get("WORKER_METRICS_PORT", metrics.DEFAULT_METRICS_PORT))

    heartbeat_conn = await asyncpg.connect(database_url)
    # A separate connection per loop: asyncpg connections aren't safe for
    # concurrent queries from two coroutines at once, and these loops run
    # concurrently for the whole process lifetime.
    queue_conn = await asyncpg.connect(database_url)
    retention_conn = await asyncpg.connect(database_url)
    price_conn = await asyncpg.connect(database_url)
    commentary_conn = await asyncpg.connect(database_url)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        metrics.start_metrics_server(metrics_port)
        logger.info(
            "worker starting (id=%s, heartbeat_interval=%ss, job_poll_interval=%ss, "
            "retention_sweep_interval=%ss, price_refresh_interval=%ss, "
            "commentary_refresh_interval=%ss, metrics_port=%s)",
            worker_id,
            HEARTBEAT_INTERVAL_SECONDS,
            JOB_POLL_INTERVAL_SECONDS,
            RETENTION_SWEEP_INTERVAL_SECONDS,
            PRICE_REFRESH_INTERVAL_SECONDS,
            COMMENTARY_REFRESH_INTERVAL_SECONDS,
            metrics_port,
        )
        await asyncio.gather(
            heartbeat_loop(heartbeat_conn, stop_event),
            job_poll_loop(queue_conn, stop_event, worker_id=worker_id),
            retention_sweep_loop(retention_conn, stop_event, blob=get_blob_storage()),
            price_refresh_loop(price_conn, stop_event, fetcher=YFinancePriceFetcher()),
            commentary_loop(commentary_conn, stop_event),
        )
    finally:
        await heartbeat_conn.close()
        await queue_conn.close()
        await retention_conn.close()
        await price_conn.close()
        await commentary_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
