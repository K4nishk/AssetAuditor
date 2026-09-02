"""Pure contribution-room ledger engine — TFSA/RRSP/FHSA.

See docs/vault/20-domain/Contribution-Rooms.md for the rules and
data/samples/README.md for the golden reconciliation numbers.
"""

from app.domain.rooms.cra_limits import DEFAULT_LIMITS_TABLE, CraLimitsTable
from app.domain.rooms.engine import compute_rooms, fhsa_year_contribution_cap
from app.domain.rooms.models import (
    AccountType,
    RoomBreakdown,
    RoomEvent,
    RoomEventKind,
    RoomLedgerEntry,
    RoomsResult,
    UserFacts,
)

__all__ = [
    "DEFAULT_LIMITS_TABLE",
    "AccountType",
    "CraLimitsTable",
    "RoomBreakdown",
    "RoomEvent",
    "RoomEventKind",
    "RoomLedgerEntry",
    "RoomsResult",
    "UserFacts",
    "compute_rooms",
    "fhsa_year_contribution_cap",
]
