"""Dashboard reads — latest gold snapshot, term buckets, diversification cuts
(KCH-59 / AA-22).

Read-only; the gold tables are written by `worker.gold.rebuild_gold`
(KCH-53 / AA-18), already CAD-reconciled by `app.domain.dashboard
.reconcile_assets_to_cad`. Runs on the RLS-scoped per-request connection,
same convention as `app.db.queries.room_events`.
"""

from __future__ import annotations

from datetime import date

import asyncpg

_LATEST_SNAPSHOT_SQL = """
    select snapshot_date, total_assets_cad, total_liabilities_cad, net_worth_cad, run_id
    from public.networth_snapshots
    where user_id = $1 and deactivated_at is null
    order by snapshot_date desc
    limit 1
"""

_TERM_BUCKETS_SQL = """
    select bucket, amount_cad, run_id
    from public.term_buckets
    where user_id = $1 and snapshot_date = $2 and deactivated_at is null
    order by bucket
"""

_DIVERSIFICATION_CUTS_SQL = """
    select label, amount_cad, run_id
    from public.diversification_cuts
    where user_id = $1 and snapshot_date = $2 and cut = $3 and deactivated_at is null
    order by amount_cad desc
"""


async def get_latest_snapshot(conn: asyncpg.Connection, *, user_id: str) -> asyncpg.Record | None:
    return await conn.fetchrow(_LATEST_SNAPSHOT_SQL, user_id)


async def list_term_buckets(
    conn: asyncpg.Connection, *, user_id: str, snapshot_date: date
) -> list[asyncpg.Record]:
    return await conn.fetch(_TERM_BUCKETS_SQL, user_id, snapshot_date)


async def list_diversification_cuts(
    conn: asyncpg.Connection, *, user_id: str, snapshot_date: date, cut: str
) -> list[asyncpg.Record]:
    return await conn.fetch(_DIVERSIFICATION_CUTS_SQL, user_id, snapshot_date, cut)


__all__ = ["get_latest_snapshot", "list_term_buckets", "list_diversification_cuts"]
