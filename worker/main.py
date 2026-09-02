"""ETL worker entrypoint — home-lab GPU box (ADR v1.1.0).

AA-4 (deploy rails) wires the heartbeat loop below to prove the box can reach
Supabase from docker-compose: `bring-up.md`'s smoke test. Polling `etl_jobs`
(`FOR UPDATE SKIP LOCKED`) and a `/metrics` endpoint are AA-11 / AA-34's job —
both extend `main()`'s loop rather than replacing it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

import asyncpg

HEARTBEAT_INTERVAL_SECONDS = 30

logger = logging.getLogger("worker")

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


async def heartbeat_loop(conn: asyncpg.Connection, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await send_heartbeat(conn)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except TimeoutError:
            pass


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    database_url = os.environ["WORKER_DATABASE_URL"]

    conn = await asyncpg.connect(database_url)
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        logger.info("worker heartbeat starting (interval=%ss)", HEARTBEAT_INTERVAL_SECONDS)
        await heartbeat_loop(conn, stop_event)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
