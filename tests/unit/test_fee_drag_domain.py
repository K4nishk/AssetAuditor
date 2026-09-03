"""Pure-math tests for app.domain.fee_drag (KCH-63 / AA-26)."""

from decimal import Decimal

from app.domain.fee_drag import BENCHMARK_MER_PCT, FeeHolding, compute_fee_drag


def test_compute_fee_drag_computes_annual_and_benchmark_cost():
    holdings = [
        FeeHolding(
            ticker="TD-BALANCED-GROWTH",
            market_value_cad=Decimal("7199.00"),
            mer_pct=Decimal("2.18"),
        )
    ]

    rows = compute_fee_drag(holdings)

    assert len(rows) == 1
    row = rows[0]
    assert row.ticker == "TD-BALANCED-GROWTH"
    assert row.mer_pct == Decimal("2.18")
    assert row.benchmark_mer_pct == BENCHMARK_MER_PCT
    assert row.annual_cost_cad == Decimal("7199.00") * Decimal("2.18") / Decimal("100")
    assert row.benchmark_cost_cad == Decimal("7199.00") * Decimal("0.20") / Decimal("100")
    assert row.excess_cost_cad == row.annual_cost_cad - row.benchmark_cost_cad
    assert row.excess_cost_cad > Decimal("0")


def test_compute_fee_drag_sorts_worst_offender_first():
    holdings = [
        FeeHolding(ticker="CHEAP", market_value_cad=Decimal("10000"), mer_pct=Decimal("0.10")),
        FeeHolding(ticker="EXPENSIVE", market_value_cad=Decimal("10000"), mer_pct=Decimal("2.50")),
        FeeHolding(ticker="MID", market_value_cad=Decimal("10000"), mer_pct=Decimal("1.00")),
    ]

    rows = compute_fee_drag(holdings)

    assert [row.ticker for row in rows] == ["EXPENSIVE", "MID", "CHEAP"]


def test_compute_fee_drag_a_fund_cheaper_than_benchmark_has_negative_excess():
    holdings = [FeeHolding(ticker="XIC", market_value_cad=Decimal("5000"), mer_pct=Decimal("0.06"))]

    rows = compute_fee_drag(holdings)

    assert rows[0].excess_cost_cad < Decimal("0")


def test_compute_fee_drag_honors_a_custom_benchmark():
    holdings = [FeeHolding(ticker="X", market_value_cad=Decimal("1000"), mer_pct=Decimal("1.00"))]

    rows = compute_fee_drag(holdings, benchmark_mer_pct=Decimal("0.50"))

    assert rows[0].benchmark_mer_pct == Decimal("0.50")
    assert rows[0].benchmark_cost_cad == Decimal("1000") * Decimal("0.50") / Decimal("100")


def test_compute_fee_drag_empty_input_returns_empty_list():
    assert compute_fee_drag([]) == []
