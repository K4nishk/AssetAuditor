"""Unit tests for the pure profile domain helpers (KCH-42 / AA-7)."""

from __future__ import annotations

from decimal import Decimal

from app.domain.profile import shows_room_widgets, to_user_facts
from app.domain.rooms.models import UserFacts


def test_shows_room_widgets_true_for_canada():
    assert shows_room_widgets("CA") is True


def test_shows_room_widgets_false_for_any_other_country():
    assert shows_room_widgets("US") is False
    assert shows_room_widgets("") is False


def test_to_user_facts_adapts_a_profile_row_shaped_mapping():
    profile = {
        "id": "u1",
        "age": 35,
        "holdings_country": "CA",
        "year_in_canada": 2009,
        "fhsa_opened_year": 2024,
        "risk_profile": "medium",
        "prior_year_earned_income": Decimal("85000.00"),
    }

    facts = to_user_facts(profile)

    assert facts == UserFacts(
        age=35,
        year_in_canada=2009,
        fhsa_opened_year=2024,
        prior_year_earned_income=Decimal("85000.00"),
    )


def test_to_user_facts_carries_none_fhsa_and_income_through():
    profile = {
        "age": 22,
        "year_in_canada": 2019,
        "fhsa_opened_year": None,
        "prior_year_earned_income": None,
    }

    facts = to_user_facts(profile)

    assert facts.fhsa_opened_year is None
    assert facts.prior_year_earned_income is None
