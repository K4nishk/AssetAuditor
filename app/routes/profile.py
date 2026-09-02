"""Profile CRUD + onboarding facts (KCH-42 / AA-7).

`users_profile` (migration 0001) is a singleton per user — `id = auth.uid()`,
no separate `user_id` column — so create and update collapse into one
idempotent `PUT` upsert instead of separate POST/PATCH verbs
(`app.db.queries.users_profile.upsert_profile`); deactivate/delete is AA-10's
scope, not this one's. `GET` 404s when no row exists yet, which is exactly
the "no profile" signal the frontend's onboarding screen (wireframe v1
screen 1) uses to show the intake form instead of the rest of the app.

`ProfileOut.shows_room_widgets` (`app.domain.profile.shows_room_widgets`)
tells the frontend whether to render the TFSA/RRSP/FHSA room widgets — the
CA-only assumption lives here, once, instead of a country-string comparison
duplicated in the frontend.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import users_profile
from app.domain.profile import shows_room_widgets

router = APIRouter(prefix="/api/profile", tags=["profile"])

RiskProfile = Literal["very_risky", "high", "medium", "low", "no_risk"]


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class ProfileUpsertRequest(BaseModel):
    age: int = Field(ge=0, le=120)
    holdings_country: str = Field(pattern=r"^[A-Z]{2}$")
    year_in_canada: int = Field(ge=1900, le=2100)
    fhsa_opened_year: int | None = Field(default=None, ge=1900, le=2100)
    risk_profile: RiskProfile
    prior_year_earned_income: Decimal | None = Field(default=None, ge=0)


class ProfileOut(BaseModel):
    age: int
    holdings_country: str
    year_in_canada: int
    fhsa_opened_year: int | None
    risk_profile: str
    prior_year_earned_income: Decimal | None
    shows_room_widgets: bool


def _to_out(row: asyncpg.Record) -> ProfileOut:
    return ProfileOut(
        age=row["age"],
        holdings_country=row["holdings_country"],
        year_in_canada=row["year_in_canada"],
        fhsa_opened_year=row["fhsa_opened_year"],
        risk_profile=row["risk_profile"],
        prior_year_earned_income=row["prior_year_earned_income"],
        shows_room_widgets=shows_room_widgets(row["holdings_country"]),
    )


@router.get("", response_model=ProfileOut)
async def get_profile(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ProfileOut:
    row = await users_profile.get_profile(conn, user_id=user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no profile yet")
    return _to_out(row)


@router.put("", response_model=ProfileOut)
async def upsert_profile(
    body: ProfileUpsertRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ProfileOut:
    row = await users_profile.upsert_profile(
        conn,
        user_id=user_id,
        age=body.age,
        holdings_country=body.holdings_country,
        year_in_canada=body.year_in_canada,
        fhsa_opened_year=body.fhsa_opened_year,
        risk_profile=body.risk_profile,
        prior_year_earned_income=body.prior_year_earned_income,
    )
    if row is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "profile is deactivated")
    return _to_out(row)
