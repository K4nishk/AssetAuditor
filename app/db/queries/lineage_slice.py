"""Drill-down reads: gold slice -> run_id -> job -> bronze file (KCH-60 / AA-23).

Every query runs on the RLS-scoped per-request connection
(`app.db.pool.rls_connection`), same convention as `app.db.queries.dashboard`.
`app.db.queries.staged_rows.list_rows_for_job` and `app.db.queries.etl_jobs.get_job`
already cover the last two hops of the chain (job -> its staged rows, job ->
its bronze_file_id) and are reused directly by `app.routes.lineage` rather
than duplicated here.
"""

from __future__ import annotations

from datetime import date

import asyncpg

_TERM_BUCKET_RUN_ID_SQL = """
    select run_id
    from public.term_buckets
    where user_id = $1 and snapshot_date = $2 and bucket = $3 and deactivated_at is null
"""

_DIVERSIFICATION_CUT_RUN_ID_SQL = """
    select run_id
    from public.diversification_cuts
    where user_id = $1 and snapshot_date = $2 and cut = $3 and label = $4
        and deactivated_at is null
"""

_NET_WORTH_RUN_ID_SQL = """
    select run_id
    from public.networth_snapshots
    where user_id = $1 and snapshot_date = $2 and deactivated_at is null
"""

# One `run_id` is exactly one `etl_jobs` processing attempt
# (`worker.lineage.LineageEmitter`'s module docstring: "One instance per
# etl_jobs processing attempt"), so every event sharing a run_id shares the
# same job_id where one was bound at all — earliest occurred_at is enough,
# no aggregation needed. `job_id is not null` skips steps emitted by a
# job-less run (e.g. a gold rebuild not triggered by any one confirm).
_JOB_ID_FOR_RUN_SQL = """
    select job_id
    from public.lineage_events
    where user_id = $1 and run_id = $2 and job_id is not null
    order by occurred_at asc
    limit 1
"""

_GET_BRONZE_FILE_SQL = """
    select id, institution, period, blob_url, purged_at
    from public.bronze_files
    where user_id = $1 and id = $2
"""


async def find_run_id_for_term_bucket(
    conn: asyncpg.Connection, *, user_id: str, snapshot_date: date, bucket: str
) -> str | None:
    row = await conn.fetchrow(_TERM_BUCKET_RUN_ID_SQL, user_id, snapshot_date, bucket)
    return str(row["run_id"]) if row is not None else None


async def find_run_id_for_diversification_cut(
    conn: asyncpg.Connection, *, user_id: str, snapshot_date: date, cut: str, label: str
) -> str | None:
    row = await conn.fetchrow(
        _DIVERSIFICATION_CUT_RUN_ID_SQL, user_id, snapshot_date, cut, label
    )
    return str(row["run_id"]) if row is not None else None


async def find_run_id_for_net_worth(
    conn: asyncpg.Connection, *, user_id: str, snapshot_date: date
) -> str | None:
    row = await conn.fetchrow(_NET_WORTH_RUN_ID_SQL, user_id, snapshot_date)
    return str(row["run_id"]) if row is not None else None


async def find_job_id_for_run(
    conn: asyncpg.Connection, *, user_id: str, run_id: str
) -> str | None:
    row = await conn.fetchrow(_JOB_ID_FOR_RUN_SQL, user_id, run_id)
    return str(row["job_id"]) if row is not None else None


async def get_bronze_file(
    conn: asyncpg.Connection, *, user_id: str, bronze_file_id: str
) -> asyncpg.Record | None:
    return await conn.fetchrow(_GET_BRONZE_FILE_SQL, user_id, bronze_file_id)


__all__ = [
    "find_run_id_for_term_bucket",
    "find_run_id_for_diversification_cut",
    "find_run_id_for_net_worth",
    "find_job_id_for_run",
    "get_bronze_file",
]
