"""Dashboard route: KPI row + three pies (KCH-59 / AA-22).

Reads the latest gold snapshot `worker.gold.rebuild_gold` (KCH-53 / AA-18,
now FX-reconciled via `app.domain.dashboard.reconcile_assets_to_cad`, AA-21)
wrote for this user. Three pies come off two gold tables plus the snapshot
itself:

- **term-buckets** — `public.term_buckets` rows as-is (short/medium/long/
  liabilities), one fixed shape.
- **net-worth distribution** — assets vs. liabilities, derived on the
  frontend straight from `kpis.total_assets_cad`/`total_liabilities_cad`;
  no separate query needed, those two numbers *are* the two slices.
- **diversification (cut switcher)** — `public.diversification_cuts`
  filtered to one `cut` dimension at a time (`?cut=institution|account_type
  |currency`, `app.domain.dashboard.AVAILABLE_CUTS`).

Every amount here is already CAD — this route does no math beyond picking
rows, per CLAUDE.md's provenance rule that a dashboard number must drill
down to its source row unchanged (drill-down itself is AA-23's route).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import dashboard as dashboard_queries
from app.domain.dashboard import AVAILABLE_CUTS, DEFAULT_CUT

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class KpisOut(BaseModel):
    total_assets_cad: Decimal
    total_liabilities_cad: Decimal
    net_worth_cad: Decimal


class TermBucketSliceOut(BaseModel):
    bucket: str
    amount_cad: Decimal


class DiversificationSliceOut(BaseModel):
    label: str
    amount_cad: Decimal


class DashboardOut(BaseModel):
    as_of: date
    kpis: KpisOut
    term_buckets: list[TermBucketSliceOut]
    diversification_cut: str
    available_cuts: list[str]
    diversification: list[DiversificationSliceOut]


@router.get("", response_model=DashboardOut)
async def get_dashboard(
    cut: str = Query(DEFAULT_CUT),
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> DashboardOut:
    if cut not in AVAILABLE_CUTS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"unknown diversification cut: {cut!r}"
        )

    snapshot = await dashboard_queries.get_latest_snapshot(conn, user_id=user_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no dashboard data yet")

    snapshot_date = snapshot["snapshot_date"]
    buckets = await dashboard_queries.list_term_buckets(
        conn, user_id=user_id, snapshot_date=snapshot_date
    )
    cuts = await dashboard_queries.list_diversification_cuts(
        conn, user_id=user_id, snapshot_date=snapshot_date, cut=cut
    )

    return DashboardOut(
        as_of=snapshot_date,
        kpis=KpisOut(
            total_assets_cad=snapshot["total_assets_cad"],
            total_liabilities_cad=snapshot["total_liabilities_cad"],
            net_worth_cad=snapshot["net_worth_cad"],
        ),
        term_buckets=[
            TermBucketSliceOut(bucket=row["bucket"], amount_cad=row["amount_cad"])
            for row in buckets
        ],
        diversification_cut=cut,
        available_cuts=list(AVAILABLE_CUTS),
        diversification=[
            DiversificationSliceOut(label=row["label"], amount_cad=row["amount_cad"])
            for row in cuts
        ],
    )
