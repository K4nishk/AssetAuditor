"""Risk-profile-dependent diversification flags (KCH-61 / AA-24)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.diversification_flags import (
    PortfolioHolding,
    UnknownRiskProfileError,
    compute_diversification_flags,
)


def _holding(**overrides) -> PortfolioHolding:
    row = {"ticker": None, "institution": "scotia", "amount_cad": Decimal("1000")}
    row.update(overrides)
    return PortfolioHolding(**row)


def test_unknown_risk_profile_raises() -> None:
    with pytest.raises(UnknownRiskProfileError):
        compute_diversification_flags([], risk_profile="reckless")


def test_empty_portfolio_flags_nothing_triggered() -> None:
    flags = compute_diversification_flags([], risk_profile="medium")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")
    assert crypto.weight_pct == Decimal("0")
    assert crypto.is_triggered is False


def test_crypto_over_ten_percent_flags_for_medium_profile() -> None:
    # mirrors data/samples/README.md's mock-user shape: crypto is ~11% of the
    # non-home portfolio, which is exactly what the e2e skill's "crypto >10%
    # flagged for medium" checklist line exercises.
    holdings = [
        _holding(institution="scotia", amount_cad=Decimal("63449")),
        _holding(institution="kraken", amount_cad=Decimal("8800")),
        _holding(institution="moomoo", amount_cad=Decimal("1800")),
        _holding(institution="canadalife", amount_cad=Decimal("22500")),
    ]
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")

    assert crypto.threshold_pct == Decimal("10")
    assert crypto.weight_pct > Decimal("10")
    assert crypto.is_triggered is True
    assert crypto.is_muted is False
    assert "%" in crypto.message


def test_crypto_under_threshold_for_high_profile_is_not_triggered() -> None:
    holdings = [
        _holding(institution="scotia", amount_cad=Decimal("90000")),
        _holding(institution="kraken", amount_cad=Decimal("10000")),
    ]
    flags = compute_diversification_flags(holdings, risk_profile="high")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")
    assert crypto.threshold_pct == Decimal("15")
    assert crypto.is_triggered is False


def test_no_risk_profile_flags_any_crypto_at_all() -> None:
    holdings = [
        _holding(institution="scotia", amount_cad=Decimal("99000")),
        _holding(institution="kraken", amount_cad=Decimal("1000")),
    ]
    flags = compute_diversification_flags(holdings, risk_profile="no_risk")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")
    assert crypto.threshold_pct == Decimal("0")
    assert crypto.is_triggered is True


def test_very_risky_profile_mutes_flags_but_still_returns_them() -> None:
    holdings = [
        _holding(institution="kraken", amount_cad=Decimal("5000")),
        _holding(institution="scotia", amount_cad=Decimal("5000")),
    ]
    flags = compute_diversification_flags(holdings, risk_profile="very_risky")
    crypto = next(f for f in flags if f.kind == "crypto_concentration")
    assert crypto.is_muted is True
    assert crypto.is_triggered is True  # 50% > the 15% threshold, just muted for display


def test_employer_concentration_flag_only_emitted_when_esop_present() -> None:
    no_esop = compute_diversification_flags(
        [_holding(institution="scotia", amount_cad=Decimal("1000"))], risk_profile="medium"
    )
    assert not any(f.kind == "employer_concentration" for f in no_esop)

    with_esop = compute_diversification_flags(
        [
            _holding(institution="scotia", amount_cad=Decimal("8000")),
            _holding(institution="equateaccess", amount_cad=Decimal("2000")),
        ],
        risk_profile="medium",
    )
    employer = next(f for f in with_esop if f.kind == "employer_concentration")
    assert employer.weight_pct == Decimal("20")
    assert employer.threshold_pct == Decimal("10")
    assert employer.is_triggered is True


def test_sector_concentration_uses_etf_look_through() -> None:
    # XIC is 100% Canada, financials-heavy (33%) — a $100k XIC-only portfolio
    # should flag financials (>30%) but not, say, healthcare (3% << 30%).
    holdings = [_holding(ticker="XIC.TO", institution="questrade", amount_cad=Decimal("100000"))]
    flags = compute_diversification_flags(holdings, risk_profile="medium")

    financials = next(
        f for f in flags if f.kind == "sector_concentration" and f.label == "financials"
    )
    assert financials.weight_pct == Decimal("33")
    assert financials.is_triggered is True

    healthcare = next(
        (f for f in flags if f.kind == "sector_concentration" and f.label == "healthcare"), None
    )
    assert healthcare is not None
    assert healthcare.is_triggered is False


def test_unclassified_fund_contributes_to_total_but_no_named_sector() -> None:
    holdings = [_holding(ticker="XGRO.TO", institution="wealthsimple", amount_cad=Decimal("50000"))]
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    assert not any(f.kind == "sector_concentration" for f in flags)
    assert not any(f.kind == "home_bias" for f in flags)


def test_home_bias_uses_canada_geography_look_through() -> None:
    holdings = [_holding(ticker="XIC.TO", institution="questrade", amount_cad=Decimal("40000"))]
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    home_bias = next(f for f in flags if f.kind == "home_bias")
    assert home_bias.label == "canada"
    assert home_bias.weight_pct == Decimal("100")
    assert home_bias.is_triggered is True


def test_home_bias_absent_when_no_classified_geography_data() -> None:
    holdings = [_holding(ticker="AAPL", institution="questrade", amount_cad=Decimal("5000"))]
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    assert not any(f.kind == "home_bias" for f in flags)


def test_cash_and_unticketed_holdings_never_contribute_to_sector_or_geography() -> None:
    holdings = [_holding(ticker=None, institution="scotia", amount_cad=Decimal("4200"))]
    flags = compute_diversification_flags(holdings, risk_profile="medium")
    assert not any(f.kind in ("sector_concentration", "home_bias") for f in flags)
