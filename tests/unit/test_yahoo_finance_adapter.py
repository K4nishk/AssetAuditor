"""Yahoo Finance portfolio-export CSV parser tests (KCH-55 / AA-20)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from worker.adapters.base import AdapterParseError
from worker.adapters.yahoo_finance import YahooFinanceLot, detect, parse_lots

_HEADER = (
    "Symbol,Current Price,Date,Time,Change,Open,High,Low,Volume,"
    "Trade Date,Purchase Price,Quantity,Commission,High Limit,Low Limit,Comment"
)
_VALID_CSV = (
    f"{_HEADER}\n"
    "AAPL,190.00,9/1/2026,4:00pm,+1.20,188.00,191.00,187.50,50000000,"
    "3/14/2024,171.20,4,4.95,,,\n"
    "AAPL,190.00,9/1/2026,4:00pm,+1.20,188.00,191.00,187.50,50000000,"
    "1/10/2025,186.10,6,4.95,,,\n"
    "VFV.TO,130.00,9/1/2026,4:00pm,+0.50,129.00,131.00,128.50,1000000,"
    "6/2/2024,118.40,30,0.00,,,\n"
)


def test_detect_true_on_a_yahoo_finance_export() -> None:
    assert detect(_VALID_CSV.encode()) is True


def test_detect_false_on_an_unrelated_csv() -> None:
    assert detect(b"a,b,c\n1,2,3\n") is False


def test_parse_lots_returns_one_lot_per_row_as_decimal() -> None:
    lots = parse_lots(_VALID_CSV.encode())

    assert lots == [
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
    for lot in lots:
        assert isinstance(lot.quantity, Decimal)
        assert isinstance(lot.unit_cost, Decimal)


def test_parse_lots_skips_blank_symbol_rows() -> None:
    csv_text = f"{_HEADER}\n,190.00,9/1/2026,4:00pm,,,,,,,,,,,,\n"
    assert parse_lots(csv_text.encode()) == []


def test_parse_lots_rejects_missing_required_columns() -> None:
    with pytest.raises(AdapterParseError):
        parse_lots(b"Symbol,Quantity\nAAPL,4\n")


def test_parse_lots_rejects_unrecognized_trade_date_format() -> None:
    csv_text = f"{_HEADER}\nAAPL,,,,,,,,,2024-03-14,171.20,4,,,,\n"
    with pytest.raises(AdapterParseError):
        parse_lots(csv_text.encode())
