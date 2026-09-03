"""ETF factsheet-weights seed table (KCH-61 / AA-24)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.etf_classification import GEOGRAPHIES, SECTORS, classify_fund

KNOWN_TICKERS = ("XEQT", "VEQT", "VFV", "XIC")


@pytest.mark.parametrize("ticker", KNOWN_TICKERS)
def test_known_etf_sector_weights_sum_to_one(ticker: str) -> None:
    classification = classify_fund(ticker)
    assert classification is not None
    assert sum(classification.sector_weights.values(), Decimal("0")) == Decimal("1")


@pytest.mark.parametrize("ticker", KNOWN_TICKERS)
def test_known_etf_geography_weights_sum_to_one(ticker: str) -> None:
    classification = classify_fund(ticker)
    assert classification is not None
    assert sum(classification.geography_weights.values(), Decimal("0")) == Decimal("1")


@pytest.mark.parametrize("ticker", KNOWN_TICKERS)
def test_known_etf_weight_keys_match_the_documented_taxonomy(ticker: str) -> None:
    classification = classify_fund(ticker)
    assert classification is not None
    assert set(classification.sector_weights) <= set(SECTORS)
    assert set(classification.geography_weights) <= set(GEOGRAPHIES)


def test_classify_fund_strips_to_exchange_suffix() -> None:
    assert classify_fund("VFV.TO") == classify_fund("VFV")
    assert classify_fund("xic.to") == classify_fund("XIC")


def test_unknown_fund_is_unclassified() -> None:
    assert classify_fund("XGRO.TO") is None
    assert classify_fund("AAPL") is None


def test_xic_is_canada_only_and_vfv_is_us_only() -> None:
    xic = classify_fund("XIC")
    vfv = classify_fund("VFV")
    assert xic is not None and vfv is not None
    assert xic.geography_weights == {"canada": Decimal("1")}
    assert vfv.geography_weights == {"us": Decimal("1")}
