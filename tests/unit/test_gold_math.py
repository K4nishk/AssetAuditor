"""Pure net-worth/term-bucket/diversification math (KCH-53 / AA-18)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.gold import (
    AssetValuation,
    CashTransaction,
    LiabilityAmount,
    LotForValuation,
    compute_cash_balance,
    compute_gold_totals,
    value_holding,
)
from app.domain.rooms.engine import compute_rooms
from app.domain.rooms.links import ContributionTransaction, derive_contribution_room_events
from app.domain.rooms.models import RoomEvent, UserFacts


def test_value_holding_falls_back_to_avg_cost_when_no_lots() -> None:
    assert value_holding(Decimal("10"), Decimal("100.50"), []) == Decimal("1005.00")


def test_value_holding_zero_when_avg_cost_missing_and_no_lots() -> None:
    assert value_holding(Decimal("10"), None, []) == Decimal("0")


def test_value_holding_sums_lots_when_present() -> None:
    lots = [
        LotForValuation(quantity=Decimal("4"), unit_cost=Decimal("171.20"), vested=None),
        LotForValuation(quantity=Decimal("6"), unit_cost=Decimal("186.10"), vested=None),
    ]
    # lots win over avg_cost (999) when both are available
    assert value_holding(Decimal("10"), Decimal("999"), lots) == Decimal("1801.40")


def test_value_holding_excludes_unvested_esop_lots() -> None:
    lots = [
        LotForValuation(quantity=Decimal("45"), unit_cost=Decimal("38"), vested=True),
        LotForValuation(quantity=Decimal("30"), unit_cost=Decimal("38"), vested=False),
    ]
    assert value_holding(Decimal("75"), Decimal("38"), lots) == Decimal("1710")


def test_value_holding_drops_lots_missing_unit_cost() -> None:
    lots = [
        LotForValuation(quantity=Decimal("10"), unit_cost=Decimal("5"), vested=None),
        LotForValuation(quantity=Decimal("10"), unit_cost=None, vested=None),
    ]
    assert value_holding(Decimal("20"), Decimal("1"), lots) == Decimal("50")


def test_compute_cash_balance_nets_credits_and_debits() -> None:
    txns = [
        CashTransaction(kind="credit", amount=Decimal("3450.00")),
        CashTransaction(kind="debit", amount=Decimal("1000.00")),
        CashTransaction(kind="credit", amount=Decimal("50.00")),
    ]
    assert compute_cash_balance(txns) == Decimal("2500.00")


def test_compute_cash_balance_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unexpected cash transaction kind"):
        compute_cash_balance([CashTransaction(kind="buy", amount=Decimal("1"))])


def test_compute_gold_totals_net_worth_and_buckets() -> None:
    assets = [
        AssetValuation(
            account_type="chequing",
            institution="scotia",
            currency="CAD",
            amount_cad=Decimal("4200"),
        ),
        AssetValuation(
            account_type="tfsa",
            institution="questrade",
            currency="CAD",
            amount_cad=Decimal("6500"),
        ),
        AssetValuation(
            account_type="esop",
            institution="equateaccess",
            currency="CAD",
            amount_cad=Decimal("1710"),
        ),
    ]
    liabilities = [LiabilityAmount(balance_cad=Decimal("9800"))]

    totals = compute_gold_totals(assets, liabilities)

    assert totals.total_assets_cad == Decimal("12410")
    assert totals.total_liabilities_cad == Decimal("9800")
    assert totals.net_worth_cad == Decimal("2610")
    assert totals.term_buckets == {
        "short_term": Decimal("4200"),
        "long_term": Decimal("8210"),
        "liabilities": Decimal("9800"),
    }
    assert totals.diversification_cuts[("institution", "scotia")] == Decimal("4200")
    assert totals.diversification_cuts[("account_type", "tfsa")] == Decimal("6500")
    assert totals.diversification_cuts[("currency", "CAD")] == Decimal("12410")


def test_compute_gold_totals_no_liabilities_bucket_when_no_liabilities() -> None:
    assets = [
        AssetValuation(
            account_type="hisa",
            institution="wealthsimple",
            currency="CAD",
            amount_cad=Decimal("15000"),
        )
    ]
    totals = compute_gold_totals(assets, [])
    assert "liabilities" not in totals.term_buckets
    assert totals.total_liabilities_cad == Decimal("0")
    assert totals.net_worth_cad == Decimal("15000")


def test_gold_rebuild_pipeline_reproduces_tfsa_and_fhsa_golden_numbers() -> None:
    """End-to-end through the *pure* pieces `worker.gold.rebuild_gold` wires
    together: confirmed contribution transactions -> derived room_events ->
    `app.domain.rooms.engine.compute_rooms` -> the golden room figures in
    `data/samples/README.md` (TFSA $41,200 room from a $10,300 TFSA
    contribution; FHSA $12,000 room). Uses hand-authored transactions rather
    than the real fixture adapters, same as `tests/unit/test_rooms_engine.py`
    — this test is about the AA-18 derivation link being correct, not about
    reproducing a real adapter's parsed totals (no FX/price layer exists yet
    to make that meaningful; see `app.domain.gold`'s module docstring).
    """
    contribution_txns = [
        ContributionTransaction(
            transaction_id="tfsa-1",
            account_type="tfsa",
            occurred_at=datetime(2026, 6, 1, tzinfo=UTC),
            amount=Decimal("10300"),
        ),
        ContributionTransaction(
            transaction_id="fhsa-1",
            account_type="fhsa_invest",
            occurred_at=datetime(2024, 12, 31, tzinfo=UTC),
            amount=Decimal("8000"),
        ),
        ContributionTransaction(
            transaction_id="fhsa-2",
            account_type="fhsa_invest",
            occurred_at=datetime(2025, 12, 31, tzinfo=UTC),
            amount=Decimal("4000"),
        ),
    ]
    derived = derive_contribution_room_events(contribution_txns)
    room_events = [
        RoomEvent(account_type=e.account_type, year=e.year, kind="contribution", amount=e.amount)
        for e in derived
    ]

    user_facts = UserFacts(
        age=29,
        year_in_canada=2019,
        fhsa_opened_year=2024,
        prior_year_earned_income=Decimal("82000"),
    )
    result = compute_rooms(user_facts, room_events, as_of_year=2026)

    assert result.tfsa.room_remaining == Decimal("41200")
    assert result.fhsa.room_remaining == Decimal("12000")
