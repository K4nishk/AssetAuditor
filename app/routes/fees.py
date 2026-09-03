"""GET /api/fees/drag — MER comparison (fee-drag) bar (KCH-63 / AA-26).

Pairs `app.db.queries.fees.fetch_mer_by_ticker` (this issue's one new
column) with the CAD-reconciled market values
`app.db.queries.diversification.fetch_portfolio_holdings` (AA-24) already
knows how to compute. Holdings with no disclosed MER — every adapter except
`worker.adapters.td`, per that module's docstring — are silently excluded
from the comparison rather than assigned a guessed rate, same
never-guessed-only-known posture as `app.domain.etf_classification`. An
empty `rows` list (no holding has ever disclosed a MER) is a normal 200, not
an error — same "empty state, not a failure" convention
`app.routes.commentary`'s 404 uses for "no card yet".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import diversification as diversification_queries
from app.db.queries import fees as fees_queries
from app.domain.fee_drag import BENCHMARK_MER_PCT, FeeHolding, compute_fee_drag
from app.domain.prices import MissingFxRateError

router = APIRouter(prefix="/api/fees", tags=["fees"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class FeeDragRowOut(BaseModel):
    ticker: str
    mer_pct: Decimal
    benchmark_mer_pct: Decimal
    annual_cost_cad: Decimal
    benchmark_cost_cad: Decimal
    excess_cost_cad: Decimal


class FeeDragOut(BaseModel):
    benchmark_mer_pct: Decimal
    rows: list[FeeDragRowOut]
    total_annual_cost_cad: Decimal
    total_benchmark_cost_cad: Decimal


@router.get("/drag", response_model=FeeDragOut)
async def get_fee_drag(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> FeeDragOut:
    mer_by_ticker = await fees_queries.fetch_mer_by_ticker(conn, user_id=user_id)
    if not mer_by_ticker:
        return FeeDragOut(
            benchmark_mer_pct=BENCHMARK_MER_PCT,
            rows=[],
            total_annual_cost_cad=Decimal("0"),
            total_benchmark_cost_cad=Decimal("0"),
        )

    try:
        holdings = await diversification_queries.fetch_portfolio_holdings(
            conn, user_id=user_id, as_of=datetime.now(UTC).date()
        )
    except MissingFxRateError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"fee-drag unavailable: {exc}"
        ) from exc

    # A ticker can be held at more than one institution — the MER is a
    # per-fund rate, not per-account, so combine market value across
    # accounts before pairing it with the fund's single disclosed MER.
    market_value_by_ticker: dict[str, Decimal] = {}
    for holding in holdings:
        if holding.ticker not in mer_by_ticker:
            continue
        market_value_by_ticker[holding.ticker] = (
            market_value_by_ticker.get(holding.ticker, Decimal("0")) + holding.amount_cad
        )

    fee_holdings = [
        FeeHolding(ticker=ticker, market_value_cad=amount, mer_pct=mer_by_ticker[ticker])
        for ticker, amount in market_value_by_ticker.items()
    ]
    rows = compute_fee_drag(fee_holdings)

    return FeeDragOut(
        benchmark_mer_pct=BENCHMARK_MER_PCT,
        rows=[FeeDragRowOut(**vars(row)) for row in rows],
        total_annual_cost_cad=sum((row.annual_cost_cad for row in rows), Decimal("0")),
        total_benchmark_cost_cad=sum((row.benchmark_cost_cad for row in rows), Decimal("0")),
    )
