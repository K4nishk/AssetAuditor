"""Attach `room_events` provenance to the pure engine's ledger (KCH-44 / AA-9).

`app.domain.rooms.engine.compute_rooms` (AA-8, settled interface — not
modified here) returns `RoomLedgerEntry`s shaped only by
year/kind/amount/note; it is pure domain math with no notion of a database
row, so it drops the originating `room_events.id`/`source_ref` on the way
in. The rooms screen's "contributions linked to source transactions"
requirement (mvp.md AA-9; skills/e2e-testing/SKILL.md Flow 2) needs that
link back for drill-down, so this module re-attaches it from the same raw
rows the caller already fetched to build the engine's `RoomEvent` list —
matched by `(year, kind, amount)`, consumed FIFO per key so repeat entries
(e.g. two same-year same-amount contributions) each still get a distinct
row rather than all pointing at the first match. `grant` ledger lines have
no DB row (CRA limits, computed, never stored) and are left unlinked, which
is expected, not an error.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import Decimal

from app.domain.rooms.models import RoomBreakdown, RoomEventKind, RoomsResult


@dataclass(frozen=True)
class RawRoomEvent:
    """One `room_events` row, as fetched for a rooms-screen request."""

    id: str
    account_type: str
    year: int
    kind: RoomEventKind
    amount: Decimal
    source_ref: str | None


@dataclass(frozen=True)
class LinkedLedgerEntry:
    year: int
    kind: RoomEventKind
    amount: Decimal
    note: str
    room_event_id: str | None
    source_ref: str | None


@dataclass(frozen=True)
class LinkedBreakdown:
    room_total: Decimal
    room_used: Decimal
    room_remaining: Decimal
    ledger: list[LinkedLedgerEntry]


@dataclass(frozen=True)
class LinkedRoomsResult:
    tfsa: LinkedBreakdown
    rrsp: LinkedBreakdown
    fhsa: LinkedBreakdown


def _link_breakdown(breakdown: RoomBreakdown, raw_events: list[RawRoomEvent]) -> LinkedBreakdown:
    by_key: dict[tuple[int, str, Decimal], deque[RawRoomEvent]] = defaultdict(deque)
    for row in raw_events:
        by_key[(row.year, row.kind, row.amount)].append(row)

    ledger = []
    for entry in breakdown.ledger:
        queue = by_key[(entry.year, entry.kind, entry.amount)]
        source_row = queue.popleft() if queue else None
        ledger.append(
            LinkedLedgerEntry(
                year=entry.year,
                kind=entry.kind,
                amount=entry.amount,
                note=entry.note,
                room_event_id=source_row.id if source_row else None,
                source_ref=source_row.source_ref if source_row else None,
            )
        )
    return LinkedBreakdown(
        room_total=breakdown.room_total,
        room_used=breakdown.room_used,
        room_remaining=breakdown.room_remaining,
        ledger=ledger,
    )


def link_rooms_result(rooms: RoomsResult, raw_events: list[RawRoomEvent]) -> LinkedRoomsResult:
    """Enrich `compute_rooms`'s output with DB row ids for drill-down.

    `raw_events` should be the same rows (any mix of account types) used to
    build the `RoomEvent` list passed into `compute_rooms` — each breakdown
    is matched against its own `account_type` slice internally.
    """
    return LinkedRoomsResult(
        tfsa=_link_breakdown(rooms.tfsa, [e for e in raw_events if e.account_type == "tfsa"]),
        rrsp=_link_breakdown(rooms.rrsp, [e for e in raw_events if e.account_type == "rrsp"]),
        fhsa=_link_breakdown(rooms.fhsa, [e for e in raw_events if e.account_type == "fhsa"]),
    )
