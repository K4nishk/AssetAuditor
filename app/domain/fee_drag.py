"""Fee-drag comparison: disclosed MER vs. a benchmark (KCH-63 / AA-26).

Pure math only, same domain/IO split as `app.domain.gold`/`app.domain.prices`.
Every input `mer_pct` here must come from a real source statement
(`worker.adapters.td`'s `mer_pct` field, threaded through to
`public.holdings.mer_pct` by `app.db.queries.silver`) — this module never
guesses a fee for a holding that didn't disclose one, same
never-guessed-only-known posture as `app.domain.etf_classification
.classify_fund`.

`BENCHMARK_MER_PCT` is the one assumption this module makes: a typical
broad-market, low-cost Canadian-listed ETF's MER, used only as a comparison
point so the user can see what the same dollars would cost in a cheaper
fund. It is not a recommendation (CLAUDE.md's advice-shaped guardrail is
`app.domain.audit_commentary`'s concern, not this module's) — the fee-drag
bar is a plain fact ("your fund costs X, a low-cost index fund costs Y"),
not a suggestion to switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

BENCHMARK_MER_PCT = Decimal("0.20")
_PERCENT = Decimal("100")


@dataclass(frozen=True)
class FeeHolding:
    """One CAD-valued holding carrying a disclosed MER."""

    ticker: str
    market_value_cad: Decimal
    mer_pct: Decimal


@dataclass(frozen=True)
class FeeDragRow:
    ticker: str
    mer_pct: Decimal
    benchmark_mer_pct: Decimal
    annual_cost_cad: Decimal
    benchmark_cost_cad: Decimal
    excess_cost_cad: Decimal


def _annual_cost(market_value_cad: Decimal, mer_pct: Decimal) -> Decimal:
    return market_value_cad * (mer_pct / _PERCENT)


def compute_fee_drag(
    holdings: list[FeeHolding], *, benchmark_mer_pct: Decimal = BENCHMARK_MER_PCT
) -> list[FeeDragRow]:
    """One row per holding, highest excess-cost-vs-benchmark first — the
    worst fee drag is what the bar chart should draw attention to."""
    rows = [
        FeeDragRow(
            ticker=holding.ticker,
            mer_pct=holding.mer_pct,
            benchmark_mer_pct=benchmark_mer_pct,
            annual_cost_cad=_annual_cost(holding.market_value_cad, holding.mer_pct),
            benchmark_cost_cad=_annual_cost(holding.market_value_cad, benchmark_mer_pct),
            excess_cost_cad=_annual_cost(holding.market_value_cad, holding.mer_pct)
            - _annual_cost(holding.market_value_cad, benchmark_mer_pct),
        )
        for holding in holdings
    ]
    return sorted(rows, key=lambda row: row.excess_cost_cad, reverse=True)


__all__ = ["BENCHMARK_MER_PCT", "FeeDragRow", "FeeHolding", "compute_fee_drag"]
