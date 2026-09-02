"""Golden-number and edge-case tests for the contribution-room engine (KCH-43 / AA-8).

Golden numbers ($41,200 / $10,660 / $12,000) are from data/samples/README.md,
hand-computed against the "Alex Mock" fixture as of 2026-07-31.
"""

from decimal import Decimal

import pytest

from app.domain.rooms import RoomEvent, UserFacts, compute_rooms, fhsa_year_contribution_cap
from app.domain.rooms.cra_limits import DEFAULT_LIMITS_TABLE, CraLimitsTable

ALEX = UserFacts(
    age=29,
    year_in_canada=2019,
    fhsa_opened_year=2024,
    prior_year_earned_income=Decimal("82000"),
)


def test_tfsa_golden_number_alex_mock() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="contribution", amount=Decimal("10300")),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.tfsa.room_total == Decimal("51500")
    assert result.tfsa.room_used == Decimal("10300")
    assert result.tfsa.room_remaining == Decimal("41200")


def test_rrsp_golden_number_alex_mock() -> None:
    events = [
        RoomEvent(
            account_type="rrsp", year=2026, kind="pension_adjustment", amount=Decimal("4100")
        ),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.rrsp.room_total == Decimal("10660")
    assert result.rrsp.room_used == Decimal("0")
    assert result.rrsp.room_remaining == Decimal("10660")


def test_fhsa_golden_number_alex_mock() -> None:
    events = [
        RoomEvent(account_type="fhsa", year=2024, kind="contribution", amount=Decimal("8000")),
        RoomEvent(account_type="fhsa", year=2025, kind="contribution", amount=Decimal("4000")),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.fhsa.room_total == Decimal("24000")
    assert result.fhsa.room_used == Decimal("12000")
    assert result.fhsa.room_remaining == Decimal("12000")


def test_tfsa_immigrant_only_accrues_room_from_arrival_year() -> None:
    facts = UserFacts(age=40, year_in_canada=2019)
    # DEFAULT_LIMITS_TABLE's RRSP limits only start in 2025; compute_rooms
    # always computes RRSP too, so an as_of_year of 2020 needs its own limit.
    table = CraLimitsTable(
        version="test",
        tfsa_annual_limits=DEFAULT_LIMITS_TABLE.tfsa_annual_limits,
        rrsp_annual_limits={**DEFAULT_LIMITS_TABLE.rrsp_annual_limits, 2020: Decimal("27230")},
    )

    result = compute_rooms(facts, [], limits_table=table, as_of_year=2020)

    grant_years = {entry.year for entry in result.tfsa.ledger if entry.kind == "grant"}
    assert grant_years == {2019, 2020}
    assert result.tfsa.room_total == Decimal("6000") + Decimal("6000")


def test_tfsa_room_does_not_accrue_before_age_18() -> None:
    # Turns 18 in 2022 (age 22 as of 2026); arrived long before that.
    facts = UserFacts(age=22, year_in_canada=2010)

    result = compute_rooms(facts, [], as_of_year=2026)

    grant_years = {entry.year for entry in result.tfsa.ledger if entry.kind == "grant"}
    assert min(grant_years) == 2022


def test_tfsa_withdrawal_is_not_recredited_same_year() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="contribution", amount=Decimal("1000")),
        RoomEvent(account_type="tfsa", year=2026, kind="withdrawal", amount=Decimal("1000")),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.tfsa.room_used == Decimal("1000")


def test_tfsa_withdrawal_is_recredited_the_following_january() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2025, kind="contribution", amount=Decimal("1000")),
        RoomEvent(account_type="tfsa", year=2025, kind="withdrawal", amount=Decimal("1000")),
    ]

    result_same_year = compute_rooms(ALEX, events, as_of_year=2025)
    result_next_year = compute_rooms(ALEX, events, as_of_year=2026)

    assert result_same_year.tfsa.room_used == Decimal("1000")
    assert result_next_year.tfsa.room_used == Decimal("0")


def test_tfsa_cra_override_wins_and_ledger_explains_delta() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="cra_override", amount=Decimal("60000")),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.tfsa.room_total == Decimal("60000")
    override_entries = [e for e in result.tfsa.ledger if e.kind == "cra_override"]
    assert len(override_entries) == 1
    assert "delta vs computed" in override_entries[0].note


def test_rrsp_carry_forward_from_prior_grant_event() -> None:
    events = [
        RoomEvent(
            account_type="rrsp", year=2026, kind="pension_adjustment", amount=Decimal("4100")
        ),
        RoomEvent(account_type="rrsp", year=2025, kind="grant", amount=Decimal("2000")),
    ]

    result = compute_rooms(ALEX, events, as_of_year=2026)

    assert result.rrsp.room_total == Decimal("10660") + Decimal("2000")


def test_fhsa_zero_year_one_contribution_permits_16k_addable_year_two() -> None:
    cap_year_one = fhsa_year_contribution_cap(2024, opened_year=2024, contributions_by_year={})
    cap_year_two = fhsa_year_contribution_cap(
        2025, opened_year=2024, contributions_by_year={2024: Decimal("0")}
    )

    assert cap_year_one == Decimal("8000")
    assert cap_year_two == Decimal("16000")


def test_fhsa_full_year_one_contribution_caps_year_two_at_annual_limit() -> None:
    cap_year_two = fhsa_year_contribution_cap(
        2025, opened_year=2024, contributions_by_year={2024: Decimal("8000")}
    )

    assert cap_year_two == Decimal("8000")


def test_fhsa_room_never_exceeds_lifetime_cap() -> None:
    facts = UserFacts(age=40, year_in_canada=2000, fhsa_opened_year=2024)
    # DEFAULT_LIMITS_TABLE only publishes TFSA/RRSP limits through 2026;
    # compute_rooms always computes all three, so reaching the FHSA lifetime
    # cap (5 years out, 2028) needs synthetic future-year limits for the
    # other two — real values, unpublished this far out, don't matter here.
    table = CraLimitsTable(
        version="test-future",
        tfsa_annual_limits={
            **DEFAULT_LIMITS_TABLE.tfsa_annual_limits,
            2027: Decimal("7000"),
            2028: Decimal("7000"),
            2029: Decimal("7000"),
            2030: Decimal("7000"),
        },
        rrsp_annual_limits={
            **DEFAULT_LIMITS_TABLE.rrsp_annual_limits,
            2027: Decimal("33810"),
            2028: Decimal("33810"),
            2029: Decimal("33810"),
            2030: Decimal("33810"),
        },
    )

    result = compute_rooms(facts, [], limits_table=table, as_of_year=2030)

    assert result.fhsa.room_total == DEFAULT_LIMITS_TABLE.fhsa_lifetime_limit


def test_fhsa_no_open_account_has_zero_room() -> None:
    facts = UserFacts(age=40, year_in_canada=2000, fhsa_opened_year=None)

    result = compute_rooms(facts, [], as_of_year=2026)

    assert result.fhsa.room_total == Decimal("0")
    assert result.fhsa.ledger == []


def test_rrsp_limit_for_uses_closest_known_year_below_request() -> None:
    table = CraLimitsTable(
        version="test",
        rrsp_annual_limits={2024: Decimal("31560"), 2026: Decimal("33810")},
    )

    # 2025 has no published limit; must use 2024 (closest known year below),
    # not silently jump to the latest known year (2026).
    assert table.rrsp_limit_for(2025) == Decimal("31560")


def test_rrsp_limit_for_exact_match_wins() -> None:
    table = CraLimitsTable(
        version="test",
        rrsp_annual_limits={2024: Decimal("31560"), 2026: Decimal("33810")},
    )

    assert table.rrsp_limit_for(2026) == Decimal("33810")


def test_rrsp_limit_for_raises_below_earliest_known_year() -> None:
    table = CraLimitsTable(version="test", rrsp_annual_limits={2025: Decimal("32490")})

    with pytest.raises(ValueError, match="2025"):
        table.rrsp_limit_for(2020)


def test_rrsp_limit_for_raises_when_table_empty() -> None:
    table = CraLimitsTable(version="test", rrsp_annual_limits={})

    with pytest.raises(ValueError, match="empty"):
        table.rrsp_limit_for(2026)


def test_tfsa_raises_when_limits_table_has_gap() -> None:
    table = CraLimitsTable(
        version="test",
        tfsa_annual_limits={2019: Decimal("6000"), 2021: Decimal("6000")},
    )
    facts = UserFacts(age=40, year_in_canada=2019)

    with pytest.raises(ValueError, match="2020"):
        compute_rooms(facts, [], limits_table=table, as_of_year=2021)
