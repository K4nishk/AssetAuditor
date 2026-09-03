"""Risk-profile-dependent diversification flags (KCH-61 / AA-24).

Pure math only, same domain/IO split as `app.domain.dashboard`/
`app.domain.gold`. Consumes a flat list of already-CAD-valued portfolio
lines (`app.db.queries.diversification.fetch_portfolio_holdings` builds
these) and the user's declared risk profile
(`docs/vault/20-domain/Risk-Profiles.md`) and returns observations, never
directives — Diversification-Factors.md's compliance note: outputs are
*observations* ("Tech is 48% of equities") + *avenues*, never personalized
directives. Every message here states a fact, never a recommendation.

Four flag kinds, matching mvp.md's AA-24 spec:
- `crypto_concentration` — the combined share held at a crypto institution
  (Kraken/moomoo, per `docs/vault/20-domain/Institutions.md`)
- `sector_concentration` — one GICS-style sector's look-through share
  (`app.domain.etf_classification`); a fund with no seed-table entry
  contributes to the portfolio total but not to any specific sector
- `home_bias` — Canada's look-through geography share vs. its ~3% of world
  market cap (Diversification-Factors.md Dimension #3)
- `employer_concentration` — the EquateAccess ESOP institution's share
  (dedicated per Institutions.md: employer plans concentrate risk twice —
  income + equity in the same company); only emitted when an ESOP holding
  is present

Thresholds follow `docs/vault/20-domain/Risk-Profiles.md`'s table. That
table gives an explicit number only for the `medium` row (crypto >10%,
sector >30%, home bias, single-name >10%); every other profile's number
below is this module's own consistent extension of the table's qualitative
language (`no_risk` "flags anything volatile at all" -> threshold 0,
`very_risky` "mutes ... still shows them" -> same threshold as `high`,
`is_muted=True`), not a separately-sourced figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.etf_classification import SECTORS, classify_fund

ZERO = Decimal("0")
HUNDRED = Decimal("100")

CRYPTO_INSTITUTIONS = frozenset({"kraken", "moomoo"})
EMPLOYER_INSTITUTIONS = frozenset({"equateaccess"})
HOME_GEOGRAPHY = "canada"
WORLD_MARKET_CAP_BENCHMARK_PCT = Decimal("3")  # Diversification-Factors.md Dimension #3

RISK_PROFILES: tuple[str, ...] = ("very_risky", "high", "medium", "low", "no_risk")

# Shared concentration threshold per profile, reused for crypto/home_bias/
# employer_concentration; sector_concentration uses its own table below
# (Risk-Profiles.md singles out sector's >30% as distinct from the medium
# row's other >10% figures).
_CONCENTRATION_THRESHOLD_PCT: dict[str, Decimal] = {
    "very_risky": Decimal("15"),
    "high": Decimal("15"),
    "medium": Decimal("10"),
    "low": Decimal("10"),
    "no_risk": Decimal("0"),
}
_SECTOR_THRESHOLD_PCT: dict[str, Decimal] = {
    "very_risky": Decimal("30"),
    "high": Decimal("30"),
    "medium": Decimal("30"),
    "low": Decimal("30"),
    "no_risk": Decimal("0"),
}
_MUTED_PROFILES = frozenset({"very_risky"})


@dataclass(frozen=True)
class PortfolioHolding:
    """One CAD-valued portfolio line: a `holdings` position (carries its
    `ticker`) or a cash-like account's net balance (`ticker=None`)."""

    ticker: str | None
    institution: str
    amount_cad: Decimal


@dataclass(frozen=True)
class DiversificationFlag:
    kind: str  # crypto_concentration | sector_concentration | home_bias | employer_concentration
    label: str  # e.g. a sector name, or "crypto" / "canada" / "employer_stock"
    weight_pct: Decimal
    threshold_pct: Decimal
    is_triggered: bool
    is_muted: bool
    message: str


class UnknownRiskProfileError(ValueError):
    pass


def _weight_pct(amount: Decimal, total: Decimal) -> Decimal:
    if total <= ZERO:
        return ZERO
    return (amount / total) * HUNDRED


def _flag(
    *,
    kind: str,
    label: str,
    weight_pct: Decimal,
    threshold_pct: Decimal,
    muted: bool,
    message: str,
) -> DiversificationFlag:
    return DiversificationFlag(
        kind=kind,
        label=label,
        weight_pct=weight_pct,
        threshold_pct=threshold_pct,
        is_triggered=weight_pct > threshold_pct,
        is_muted=muted,
        message=message,
    )


def compute_diversification_flags(
    holdings: list[PortfolioHolding], *, risk_profile: str
) -> list[DiversificationFlag]:
    """Compute every applicable observation across `holdings`' total CAD value.

    `holdings` should be the whole portfolio (securities + cash-like
    balances) so percentages are of the whole, not a slice — a real estate
    or pension line with no adapter yet is simply absent from the input,
    same "not materialized as an asset" gap `app.domain.gold`'s own
    docstring documents for real estate.
    """
    if risk_profile not in RISK_PROFILES:
        raise UnknownRiskProfileError(f"unknown risk profile: {risk_profile!r}")

    total = sum((holding.amount_cad for holding in holdings), ZERO)
    muted = risk_profile in _MUTED_PROFILES
    concentration_threshold = _CONCENTRATION_THRESHOLD_PCT[risk_profile]
    sector_threshold = _SECTOR_THRESHOLD_PCT[risk_profile]

    flags: list[DiversificationFlag] = []

    crypto_amount = sum(
        (h.amount_cad for h in holdings if h.institution in CRYPTO_INSTITUTIONS), ZERO
    )
    crypto_weight_pct = _weight_pct(crypto_amount, total)
    flags.append(
        _flag(
            kind="crypto_concentration",
            label="crypto",
            weight_pct=crypto_weight_pct,
            threshold_pct=concentration_threshold,
            muted=muted,
            message=f"Crypto is {crypto_weight_pct:.1f}% of your portfolio.",
        )
    )

    employer_amount = sum(
        (h.amount_cad for h in holdings if h.institution in EMPLOYER_INSTITUTIONS), ZERO
    )
    if employer_amount > ZERO:
        employer_weight_pct = _weight_pct(employer_amount, total)
        flags.append(
            _flag(
                kind="employer_concentration",
                label="employer_stock",
                weight_pct=employer_weight_pct,
                threshold_pct=concentration_threshold,
                muted=muted,
                message=(
                    f"Employer stock is {employer_weight_pct:.1f}% of your portfolio — a "
                    "concentration that sits alongside your income risk in the same employer."
                ),
            )
        )

    sector_totals: dict[str, Decimal] = {}
    geography_totals: dict[str, Decimal] = {}
    for holding in holdings:
        if holding.ticker is None:
            continue
        classification = classify_fund(holding.ticker)
        if classification is None:
            continue
        for sector, weight in classification.sector_weights.items():
            sector_totals[sector] = sector_totals.get(sector, ZERO) + holding.amount_cad * weight
        for geography, weight in classification.geography_weights.items():
            geography_totals[geography] = (
                geography_totals.get(geography, ZERO) + holding.amount_cad * weight
            )

    for sector in SECTORS:
        amount = sector_totals.get(sector, ZERO)
        if amount <= ZERO:
            continue
        weight_pct = _weight_pct(amount, total)
        flags.append(
            _flag(
                kind="sector_concentration",
                label=sector,
                weight_pct=weight_pct,
                threshold_pct=sector_threshold,
                muted=muted,
                message=f"{sector.capitalize()} is {weight_pct:.1f}% of your portfolio.",
            )
        )

    canada_amount = geography_totals.get(HOME_GEOGRAPHY, ZERO)
    if canada_amount > ZERO:
        home_weight_pct = _weight_pct(canada_amount, total)
        flags.append(
            _flag(
                kind="home_bias",
                label=HOME_GEOGRAPHY,
                weight_pct=home_weight_pct,
                threshold_pct=concentration_threshold,
                muted=muted,
                message=(
                    f"Canada is {home_weight_pct:.1f}% of your equity look-through, versus "
                    f"roughly {WORLD_MARKET_CAP_BENCHMARK_PCT}% of world market cap."
                ),
            )
        )

    return flags


__all__ = [
    "RISK_PROFILES",
    "CRYPTO_INSTITUTIONS",
    "EMPLOYER_INSTITUTIONS",
    "HOME_GEOGRAPHY",
    "WORLD_MARKET_CAP_BENCHMARK_PCT",
    "PortfolioHolding",
    "DiversificationFlag",
    "UnknownRiskProfileError",
    "compute_diversification_flags",
]
