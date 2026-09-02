"""Input/output shapes for the contribution-room engine.

Field names mirror `users_profile` and `room_events` (app/db/migrations/0001_init.sql)
so DB rows can be adapted into these with no renaming.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

AccountType = Literal["tfsa", "rrsp", "fhsa"]
RoomEventKind = Literal["grant", "contribution", "withdrawal", "pension_adjustment", "cra_override"]


@dataclass(frozen=True)
class UserFacts:
    """Profile facts the engine needs — the `room_events`-relevant subset of `users_profile`."""

    age: int
    year_in_canada: int
    fhsa_opened_year: int | None = None
    prior_year_earned_income: Decimal | None = None


@dataclass(frozen=True)
class RoomEvent:
    """One ledger entry, shaped like a `room_events` row."""

    account_type: AccountType
    year: int
    kind: RoomEventKind
    amount: Decimal


@dataclass(frozen=True)
class RoomLedgerEntry:
    """One line of the engine's output ledger — a `RoomEvent` plus a running note."""

    year: int
    kind: RoomEventKind
    amount: Decimal
    note: str = ""


@dataclass(frozen=True)
class RoomBreakdown:
    room_total: Decimal
    room_used: Decimal
    room_remaining: Decimal
    ledger: list[RoomLedgerEntry] = field(default_factory=list)


@dataclass(frozen=True)
class RoomsResult:
    tfsa: RoomBreakdown
    rrsp: RoomBreakdown
    fhsa: RoomBreakdown
