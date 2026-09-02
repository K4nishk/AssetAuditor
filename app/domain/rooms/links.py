"""Derive `room_events` from confirmed silver `transactions` (KCH-53 / AA-18).

Adapters stage FHSA/TFSA/RRSP contributions as `kind="contribution"`
transactions rather than `room_events` rows directly — `staged_rows.entity`'s
check constraint (migration 0001) only allows
`transaction|holding|lot|liability|account`, so the ledger link has to
happen downstream of silver write. `worker/adapters/wealthsimple.py`'s
module docstring names this explicitly: "room-ledger derivation from these
contribution transactions is AA-17/AA-18's job". This module is that link.

Scope: only `kind="contribution"` transactions are linked here. Adapters
that stage a registered-account purchase as `kind="buy"` (e.g.
`worker/adapters/questrade.py`'s TFSA/RRSP fills) are a fills-are-the-record
data source with no separate "contribution" event in current fixtures;
treating a buy as a contribution is a real domain judgment call left to
whichever issue builds the rooms UI against real (not hand-authored)
ledgers — `tests/unit/test_rooms_engine.py`'s golden numbers already pass a
directly-constructed `RoomEvent` list, independent of this derivation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.domain.rooms.models import AccountType

# Maps a silver `accounts.account_type` string onto the room ledger's account
# category (`app.domain.rooms.models.AccountType`). Institution-specific
# product names that share a room (e.g. Wealthsimple's "fhsa_invest") map to
# the same category as the plain type. Extend as new room-eligible account
# types are staged.
_ROOM_ACCOUNT_TYPES: dict[str, AccountType] = {
    "tfsa": "tfsa",
    "rrsp": "rrsp",
    "fhsa": "fhsa",
    "fhsa_invest": "fhsa",
}


@dataclass(frozen=True)
class ContributionTransaction:
    """One confirmed `kind="contribution"` transaction, joined to its account."""

    transaction_id: str
    account_type: str
    occurred_at: datetime
    amount: Decimal


@dataclass(frozen=True)
class DerivedRoomEvent:
    """A `room_events` row this module wants written.

    `source_ref` links it back to the transaction that produced it (ADR
    v1.0.0 §7's drill-down chain) — always `kind="contribution"`, since that
    is the only room-event kind this module derives.
    """

    account_type: AccountType
    year: int
    amount: Decimal
    source_ref: str


def derive_contribution_room_events(
    transactions: list[ContributionTransaction],
) -> list[DerivedRoomEvent]:
    """Map confirmed contribution transactions onto room-ledger entries.

    A transaction whose account isn't a known room-eligible type is skipped,
    not raised on: `kind="contribution"` could in principle be staged for a
    non-registered account by a future manual-entry path (AA-20), and that
    is simply not a room event, not a data-integrity failure.
    """
    derived: list[DerivedRoomEvent] = []
    for txn in transactions:
        room_account_type = _ROOM_ACCOUNT_TYPES.get(txn.account_type.strip().lower())
        if room_account_type is None:
            continue
        derived.append(
            DerivedRoomEvent(
                account_type=room_account_type,
                year=txn.occurred_at.year,
                amount=txn.amount,
                source_ref=txn.transaction_id,
            )
        )
    return derived
