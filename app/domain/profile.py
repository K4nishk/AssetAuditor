"""Pure profile domain logic (KCH-42 / AA-7).

No I/O — `app.routes.profile` does the DB round-trip
(`app.db.queries.users_profile`) and calls into this module just to shape/
gate the facts already in hand. Field names mirror `users_profile`
(app/db/migrations/0001_init.sql), same convention `app.domain.rooms.models`
already documents for that table.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.rooms.models import UserFacts

# CRA contribution-room rules are Canada-only (docs/vault/Assumptions.md A2) —
# every other country hides the room widgets client-side instead of showing a
# result from an engine that doesn't model its tax regime. Centralized here so
# the comparison exists in exactly one place, not duplicated in the frontend.
ROOM_ENGINE_COUNTRY = "CA"


def shows_room_widgets(holdings_country: str) -> bool:
    return holdings_country == ROOM_ENGINE_COUNTRY


def to_user_facts(profile: Mapping[str, Any]) -> UserFacts:
    """Adapt a `users_profile` row (or equivalent mapping) into the
    contribution-room engine's input shape (`app.domain.rooms.engine`,
    wired up by AA-9)."""
    return UserFacts(
        age=profile["age"],
        year_in_canada=profile["year_in_canada"],
        fhsa_opened_year=profile["fhsa_opened_year"],
        prior_year_earned_income=profile["prior_year_earned_income"],
    )
