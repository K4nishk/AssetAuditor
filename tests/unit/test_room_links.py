"""Room-contribution derivation from confirmed transactions (KCH-53 / AA-18)."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.rooms.links import ContributionTransaction, derive_contribution_room_events


def _txn(account_type: str, amount: str, year: int, txn_id: str = "t1") -> ContributionTransaction:
    return ContributionTransaction(
        transaction_id=txn_id,
        account_type=account_type,
        occurred_at=datetime(year, 12, 31, tzinfo=UTC),
        amount=Decimal(amount),
    )


def test_fhsa_invest_maps_to_fhsa_room() -> None:
    derived = derive_contribution_room_events([_txn("fhsa_invest", "8000.00", 2024, "t1")])
    assert len(derived) == 1
    event = derived[0]
    assert event.account_type == "fhsa"
    assert event.year == 2024
    assert event.amount == Decimal("8000.00")
    assert event.source_ref == "t1"


def test_plain_account_type_names_map_directly() -> None:
    derived = derive_contribution_room_events(
        [_txn("tfsa", "5000", 2026, "t1"), _txn("rrsp", "2000", 2026, "t2")]
    )
    assert {e.account_type for e in derived} == {"tfsa", "rrsp"}


def test_unmapped_account_type_is_skipped_not_raised() -> None:
    derived = derive_contribution_room_events([_txn("hisa", "500", 2026)])
    assert derived == []


def test_case_and_whitespace_insensitive() -> None:
    derived = derive_contribution_room_events([_txn(" FHSA ", "100", 2026)])
    assert derived[0].account_type == "fhsa"


def test_multiple_transactions_preserve_each_source_ref() -> None:
    derived = derive_contribution_room_events(
        [_txn("fhsa", "4000", 2025, "a"), _txn("fhsa", "4000", 2025, "b")]
    )
    assert {e.source_ref for e in derived} == {"a", "b"}
    assert sum((e.amount for e in derived), Decimal("0")) == Decimal("8000")
