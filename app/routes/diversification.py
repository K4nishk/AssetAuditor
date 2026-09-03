"""Diversification flags route (KCH-61 / AA-24).

Reads current holdings/cash balances live via
`app.db.queries.diversification.fetch_portfolio_holdings` — not the gold
snapshot tables, since sector/geography ETF look-through needs each
holding's `ticker`, which the gold `diversification_cuts` table's
institution/account_type/currency cuts don't carry — plus the caller's
declared risk profile (`users_profile.risk_profile`, AA-7), then applies
`app.domain.diversification_flags.compute_diversification_flags`.
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
from app.db.queries import users_profile as users_profile_queries
from app.domain.diversification_flags import compute_diversification_flags
from app.domain.prices import MissingFxRateError

router = APIRouter(prefix="/api/diversification", tags=["diversification"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class DiversificationFlagOut(BaseModel):
    kind: str
    label: str
    weight_pct: Decimal
    threshold_pct: Decimal
    is_triggered: bool
    is_muted: bool
    message: str


class DiversificationFlagsOut(BaseModel):
    risk_profile: str
    flags: list[DiversificationFlagOut]


@router.get("/flags", response_model=DiversificationFlagsOut)
async def get_diversification_flags(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> DiversificationFlagsOut:
    profile = await users_profile_queries.get_profile(conn, user_id=user_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no profile yet")

    try:
        holdings = await diversification_queries.fetch_portfolio_holdings(
            conn, user_id=user_id, as_of=datetime.now(UTC).date()
        )
    except MissingFxRateError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"diversification flags unavailable: {exc}",
        ) from exc

    flags = compute_diversification_flags(holdings, risk_profile=profile["risk_profile"])

    return DiversificationFlagsOut(
        risk_profile=profile["risk_profile"],
        flags=[DiversificationFlagOut(**vars(flag)) for flag in flags],
    )
