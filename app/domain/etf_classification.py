"""Static ETF factsheet-weights seed table for MVP fund look-through (KCH-61 / AA-24).

`docs/vault/20-domain/Diversification-Factors.md`'s gotcha: full fund
look-through is the hard part (a future issue per `ADR_v1.1.0.md`'s
`ADR_v1.3.x` — "ETF fund look-through"). The MVP shortcut is to map a short
list of well-known, broad-market Canadian ETFs to their own published
factsheet sector/geography weights and classify anything else as "fund —
unclassified" rather than guess. Sector taxonomy and geography buckets match
that same doc's Dimensions #2/#3 exactly, so
`app.domain.diversification_flags` aggregates straight into them.

Weights below are hand-transcribed *approximations* of each ETF's public
factsheet (mid-2020s snapshot) — real weights drift monthly as the
underlying index rebalances. This table is a seed to unblock MVP
diversification flags, not a live feed; refresh it by hand against the
provider's current factsheet when precision starts to matter.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

Weights = dict[str, Decimal]

UNCLASSIFIED = "unclassified"

# GICS-style taxonomy, matching Diversification-Factors.md Dimension #2 verbatim.
SECTORS: tuple[str, ...] = (
    "tech",
    "energy",
    "financials",
    "consumer",
    "utilities",
    "telecom",
    "healthcare",
    "industrials",
    "materials",
)

# Matching Diversification-Factors.md Dimension #3 verbatim.
GEOGRAPHIES: tuple[str, ...] = ("canada", "us", "international_developed", "emerging_markets")

_EXCHANGE_SUFFIXES = (".TO", ".V", ".NE")


@dataclass(frozen=True)
class FundClassification:
    """One ETF's equity look-through, as weight fractions (0-1).

    These four seed ETFs are 100% equity, so both maps each sum to
    ``Decimal("1")`` — a non-all-equity fund added later would need its
    non-equity slice folded in explicitly rather than assumed away.
    """

    sector_weights: Weights
    geography_weights: Weights


def _pct(value: int) -> Decimal:
    return Decimal(value) / Decimal(100)


_FACTSHEETS: dict[str, FundClassification] = {
    # iShares Core Equity ETF Portfolio — all-world all-equity.
    "XEQT": FundClassification(
        sector_weights={
            "tech": _pct(22),
            "energy": _pct(6),
            "financials": _pct(20),
            "consumer": _pct(18),
            "utilities": _pct(3),
            "telecom": _pct(5),
            "healthcare": _pct(9),
            "industrials": _pct(12),
            "materials": _pct(5),
        },
        geography_weights={
            "canada": _pct(24),
            "us": _pct(44),
            "international_developed": _pct(25),
            "emerging_markets": _pct(7),
        },
    ),
    # Vanguard All-Equity ETF Portfolio — all-world all-equity, higher CA weight than XEQT.
    "VEQT": FundClassification(
        sector_weights={
            "tech": _pct(21),
            "energy": _pct(6),
            "financials": _pct(21),
            "consumer": _pct(17),
            "utilities": _pct(3),
            "telecom": _pct(5),
            "healthcare": _pct(10),
            "industrials": _pct(12),
            "materials": _pct(5),
        },
        geography_weights={
            "canada": _pct(30),
            "us": _pct(40),
            "international_developed": _pct(24),
            "emerging_markets": _pct(6),
        },
    ),
    # Vanguard S&P 500 Index ETF — US large-cap.
    "VFV": FundClassification(
        sector_weights={
            "tech": _pct(32),
            "energy": _pct(3),
            "financials": _pct(15),
            "consumer": _pct(16),
            "utilities": _pct(2),
            "telecom": _pct(9),
            "healthcare": _pct(11),
            "industrials": _pct(8),
            "materials": _pct(4),
        },
        geography_weights={"us": _pct(100)},
    ),
    # iShares Core S&P/TSX Capped Composite Index ETF — Canadian broad market
    # (financials + energy dominated, unlike the US/global funds above).
    "XIC": FundClassification(
        sector_weights={
            "tech": _pct(8),
            "energy": _pct(17),
            "financials": _pct(33),
            "consumer": _pct(8),
            "utilities": _pct(4),
            "telecom": _pct(4),
            "healthcare": _pct(3),
            "industrials": _pct(12),
            "materials": _pct(11),
        },
        geography_weights={"canada": _pct(100)},
    ),
}


def _normalize_ticker(ticker: str) -> str:
    base = ticker.strip().upper()
    for suffix in _EXCHANGE_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def classify_fund(ticker: str) -> FundClassification | None:
    """Look up `ticker`'s seeded factsheet weights, stripping a Canadian
    exchange suffix (`.TO`/`.V`/`.NE`) first.

    `None` means "fund — unclassified" per the module docstring's gotcha:
    callers must not guess a classification for it (excluded from any named
    sector/geography total, still counted in the overall portfolio total).
    """
    return _FACTSHEETS.get(_normalize_ticker(ticker))


__all__ = [
    "SECTORS",
    "GEOGRAPHIES",
    "UNCLASSIFIED",
    "FundClassification",
    "classify_fund",
]
