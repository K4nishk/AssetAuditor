"""Manual-entry draft-builder tests (KCH-55 / AA-20).

Pure, no I/O — checks the `StagedRowDraft` shapes these builders hand to
`app.db.queries.silver.write_confirmed_rows` (same natural-key convention
every CSV/JSON adapter uses, per `tests/unit/test_adapters.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.domain.manual_entry import (
    AccountBalanceInput,
    AccountInput,
    LotInput,
    ManualEntryValidationError,
    PortfolioEntryInput,
    build_account_balance_drafts,
    build_portfolio_drafts,
    build_portfolio_drafts_from_yahoo,
)
from worker.adapters.yahoo_finance import YahooFinanceLot
from worker.masking import is_masked_account_token

ACCOUNT = AccountInput(
    institution="Questrade", account_type="TFSA", account_number="1234", currency="CAD"
)


# --- build_portfolio_drafts --------------------------------------------------


def test_build_portfolio_drafts_with_explicit_avg_cost() -> None:
    entry = PortfolioEntryInput(
        account=ACCOUNT, ticker="aapl", quantity=Decimal("10"), avg_cost=Decimal("150.00")
    )

    drafts = build_portfolio_drafts(entry)

    assert [d.entity for d in drafts] == ["account", "holding"]
    account_payload = drafts[0].payload
    assert is_masked_account_token(account_payload["masked_identifier"])
    assert account_payload["masked_identifier"].endswith("1234")
    assert account_payload["institution"] == "Questrade"
    assert account_payload["account_type"] == "tfsa"

    holding_payload = drafts[1].payload
    assert holding_payload["ticker"] == "AAPL"
    assert holding_payload["account_mask"] == account_payload["masked_identifier"]
    assert holding_payload["quantity"] == Decimal("10")
    assert holding_payload["avg_cost"] == Decimal("150.00")
    for draft in drafts:
        assert draft.method == "manual_entry"
        assert draft.confidence == 1.0


def test_build_portfolio_drafts_derives_avg_cost_from_lots() -> None:
    entry = PortfolioEntryInput(
        account=ACCOUNT,
        ticker="AAPL",
        quantity=Decimal("10"),
        lots=[
            LotInput(quantity=Decimal("4"), unit_cost=Decimal("171.20"), acquired_at="2024-03-14"),
            LotInput(quantity=Decimal("6"), unit_cost=Decimal("186.10"), acquired_at="2025-01-10"),
        ],
    )

    drafts = build_portfolio_drafts(entry)

    entities = [d.entity for d in drafts]
    assert entities == ["account", "holding", "lot", "lot"]
    holding_payload = drafts[1].payload
    expected_avg_cost = (
        Decimal("4") * Decimal("171.20") + Decimal("6") * Decimal("186.10")
    ) / Decimal("10")
    assert holding_payload["avg_cost"] == expected_avg_cost
    for lot_draft in drafts[2:]:
        assert lot_draft.payload["account_mask"] == holding_payload["account_mask"]
        assert lot_draft.payload["ticker"] == "AAPL"


def test_build_portfolio_drafts_requires_avg_cost_or_lots() -> None:
    entry = PortfolioEntryInput(account=ACCOUNT, ticker="AAPL", quantity=Decimal("10"))

    with pytest.raises(ManualEntryValidationError, match="avg_cost"):
        build_portfolio_drafts(entry)


def test_build_portfolio_drafts_rejects_lots_with_no_derivable_cost() -> None:
    """Lots without a `unit_cost` derive to None, so a non-empty lot list is not
    enough — this used to build a holding with `avg_cost: None`, which
    `value_holding` reads as worthless rather than as missing data."""
    entry = PortfolioEntryInput(
        account=ACCOUNT,
        ticker="AAPL",
        quantity=Decimal("10"),
        lots=[LotInput(quantity=Decimal("5")), LotInput(quantity=Decimal("5"))],
    )

    with pytest.raises(ManualEntryValidationError, match="unit_cost"):
        build_portfolio_drafts(entry)


def test_build_portfolio_drafts_derives_cost_from_the_lots_that_have_one() -> None:
    """A partially-costed lot list is still derivable: the costed lots carry it."""
    entry = PortfolioEntryInput(
        account=ACCOUNT,
        ticker="AAPL",
        quantity=Decimal("10"),
        lots=[
            LotInput(quantity=Decimal("5")),
            LotInput(quantity=Decimal("5"), unit_cost=Decimal("30")),
        ],
    )

    holding = next(d for d in build_portfolio_drafts(entry) if d.entity == "holding")
    assert holding.payload["avg_cost"] == Decimal("30")


def test_build_portfolio_drafts_rejects_nonpositive_quantity() -> None:
    entry = PortfolioEntryInput(
        account=ACCOUNT, ticker="AAPL", quantity=Decimal("0"), avg_cost=Decimal("1")
    )

    with pytest.raises(ManualEntryValidationError, match="positive"):
        build_portfolio_drafts(entry)


def test_build_portfolio_drafts_rejects_an_account_number_with_too_few_digits() -> None:
    entry = PortfolioEntryInput(
        account=AccountInput(institution="TD", account_type="TFSA", account_number="ab"),
        ticker="AAPL",
        quantity=Decimal("1"),
        avg_cost=Decimal("1"),
    )

    with pytest.raises(ManualEntryValidationError):
        build_portfolio_drafts(entry)


# --- build_account_balance_drafts -------------------------------------------


def test_build_account_balance_drafts_positive_balance_is_a_credit() -> None:
    entry = AccountBalanceInput(account=ACCOUNT, balance=Decimal("8500.00"))
    now = datetime(2026, 7, 31, tzinfo=UTC)

    drafts = build_account_balance_drafts(entry, occurred_at=now)

    assert [d.entity for d in drafts] == ["account", "transaction"]
    txn_payload = drafts[1].payload
    assert txn_payload["kind"] == "credit"
    assert txn_payload["amount"] == Decimal("8500.00")
    assert txn_payload["account_mask"] == drafts[0].payload["masked_identifier"]
    assert txn_payload["occurred_at"] == now.isoformat()


def test_build_account_balance_drafts_negative_balance_is_a_debit() -> None:
    entry = AccountBalanceInput(account=ACCOUNT, balance=Decimal("-500.00"))

    drafts = build_account_balance_drafts(entry, occurred_at=datetime.now(UTC))

    assert drafts[1].payload["kind"] == "debit"
    assert drafts[1].payload["amount"] == Decimal("500.00")


def test_build_account_balance_drafts_rejects_zero_balance() -> None:
    entry = AccountBalanceInput(account=ACCOUNT, balance=Decimal("0"))

    with pytest.raises(ManualEntryValidationError):
        build_account_balance_drafts(entry, occurred_at=datetime.now(UTC))


# --- build_portfolio_drafts_from_yahoo --------------------------------------


def test_build_portfolio_drafts_from_yahoo_aggregates_lots_per_ticker() -> None:
    lots = [
        YahooFinanceLot(
            ticker="AAPL",
            quantity=Decimal("4"),
            unit_cost=Decimal("171.20"),
            acquired_at="2024-03-14",
        ),
        YahooFinanceLot(
            ticker="AAPL",
            quantity=Decimal("6"),
            unit_cost=Decimal("186.10"),
            acquired_at="2025-01-10",
        ),
        YahooFinanceLot(
            ticker="VFV.TO",
            quantity=Decimal("30"),
            unit_cost=Decimal("118.40"),
            acquired_at="2024-06-02",
        ),
    ]

    drafts = build_portfolio_drafts_from_yahoo(ACCOUNT, lots, currency="USD")

    by_entity = {"account": 0, "holding": 0, "lot": 0}
    for draft in drafts:
        by_entity[draft.entity] += 1
    assert by_entity == {"account": 1, "holding": 2, "lot": 3}

    holdings = {d.payload["ticker"]: d.payload for d in drafts if d.entity == "holding"}
    assert holdings["AAPL"]["quantity"] == Decimal("10")
    expected_avg_cost = (
        Decimal("4") * Decimal("171.20") + Decimal("6") * Decimal("186.10")
    ) / Decimal("10")
    assert holdings["AAPL"]["avg_cost"] == expected_avg_cost
    assert holdings["VFV.TO"]["quantity"] == Decimal("30")


def test_build_portfolio_drafts_from_yahoo_rejects_empty_lots() -> None:
    with pytest.raises(ManualEntryValidationError):
        build_portfolio_drafts_from_yahoo(ACCOUNT, [], currency="CAD")
