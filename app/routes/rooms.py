"""Contribution-room ledger + CRA-override routes (KCH-44 / AA-9).

Wraps `app.domain.rooms.engine.compute_rooms` (AA-8, settled interface — not
modified here) with the DB round-trip: `users_profile` supplies `UserFacts`
via `app.domain.profile.to_user_facts` (already shaped for this by AA-7),
`room_events` supplies the ledger. `as_of_year` is read from the wall clock
here, at the request boundary — the engine itself stays a pure function with
no I/O/wall-clock reads, per its own docstring.

`GET /api/rooms` powers the rooms screen: every room figure expandable to
its ledger, with each entry re-linked to its originating `room_events` row
(`app.domain.rooms.drilldown.link_rooms_result`) so contributions point back
to the transaction that produced them (mvp.md AA-9;
skills/e2e-testing/SKILL.md Flow 2). `POST /api/rooms/override` is the
`cra_override` reconciliation entry UI — inserts one `room_events` row and
returns the recomputed ledger so the delta the engine already computes
(`note=f"delta vs computed: ..."`) shows immediately.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import room_events as room_events_queries
from app.db.queries import users_profile
from app.domain.profile import to_user_facts
from app.domain.rooms.drilldown import LinkedBreakdown, RawRoomEvent, link_rooms_result
from app.domain.rooms.engine import compute_rooms
from app.domain.rooms.models import AccountType, RoomEvent

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class LedgerEntryOut(BaseModel):
    year: int
    kind: str
    amount: Decimal
    note: str
    room_event_id: str | None
    source_ref: str | None


class RoomBreakdownOut(BaseModel):
    room_total: Decimal
    room_used: Decimal
    room_remaining: Decimal
    ledger: list[LedgerEntryOut]


class RoomsOut(BaseModel):
    as_of_year: int
    tfsa: RoomBreakdownOut
    rrsp: RoomBreakdownOut
    fhsa: RoomBreakdownOut


class CraOverrideRequest(BaseModel):
    account_type: AccountType
    year: int = Field(ge=1900, le=2100)
    amount: Decimal = Field(ge=0)


def _breakdown_out(breakdown: LinkedBreakdown) -> RoomBreakdownOut:
    return RoomBreakdownOut(
        room_total=breakdown.room_total,
        room_used=breakdown.room_used,
        room_remaining=breakdown.room_remaining,
        ledger=[
            LedgerEntryOut(
                year=e.year,
                kind=e.kind,
                amount=e.amount,
                note=e.note,
                room_event_id=e.room_event_id,
                source_ref=e.source_ref,
            )
            for e in breakdown.ledger
        ],
    )


async def _load_rooms(conn: asyncpg.Connection, *, user_id: str) -> RoomsOut:
    profile = await users_profile.get_profile(conn, user_id=user_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no profile yet")

    rows = await room_events_queries.list_room_events(conn, user_id=user_id)
    room_events = [
        RoomEvent(
            account_type=r["account_type"], year=r["year"], kind=r["kind"], amount=r["amount"]
        )
        for r in rows
    ]
    raw_events = [
        RawRoomEvent(
            id=str(r["id"]),
            account_type=r["account_type"],
            year=r["year"],
            kind=r["kind"],
            amount=r["amount"],
            source_ref=str(r["source_ref"]) if r["source_ref"] is not None else None,
        )
        for r in rows
    ]

    as_of_year = datetime.now(UTC).year
    result = compute_rooms(to_user_facts(dict(profile)), room_events, as_of_year=as_of_year)
    linked = link_rooms_result(result, raw_events)

    return RoomsOut(
        as_of_year=as_of_year,
        tfsa=_breakdown_out(linked.tfsa),
        rrsp=_breakdown_out(linked.rrsp),
        fhsa=_breakdown_out(linked.fhsa),
    )


@router.get("", response_model=RoomsOut)
async def get_rooms(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> RoomsOut:
    return await _load_rooms(conn, user_id=user_id)


@router.post("/override", response_model=RoomsOut)
async def create_cra_override(
    body: CraOverrideRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> RoomsOut:
    await room_events_queries.insert_cra_override(
        conn,
        user_id=user_id,
        account_type=body.account_type,
        year=body.year,
        amount=body.amount,
    )
    return await _load_rooms(conn, user_id=user_id)
