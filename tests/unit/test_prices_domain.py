"""Unit tests for `app.domain.prices` (KCH-58 / AA-21)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.prices import (
    CRYPTO_CURRENCIES,
    MissingFxRateError,
    convert_to_cad,
    fx_symbol_for_currency,
    price_symbol_for_ticker,
)


def test_fx_symbol_for_fiat_currency_uses_the_x_suffix():
    assert fx_symbol_for_currency("USD") == "USDCAD=X"


def test_fx_symbol_for_currency_normalizes_case_and_whitespace():
    assert fx_symbol_for_currency(" usd ") == "USDCAD=X"


def test_fx_symbol_for_crypto_currency_uses_the_dash_suffix():
    assert fx_symbol_for_currency("BTC") == "BTC-CAD"
    assert fx_symbol_for_currency("ETH") == "ETH-CAD"


def test_crypto_currencies_covers_every_asset_the_kraken_adapter_stages():
    assert CRYPTO_CURRENCIES == {"BTC", "ETH"}


def test_convert_to_cad_passes_cad_through_unchanged_without_a_rate():
    assert convert_to_cad(Decimal("100"), currency="CAD", fx_rate=None) == Decimal("100")


def test_convert_to_cad_multiplies_by_the_fx_rate():
    result = convert_to_cad(Decimal("100"), currency="USD", fx_rate=Decimal("1.35"))
    assert result == Decimal("135.00")


def test_convert_to_cad_normalizes_currency_case():
    result = convert_to_cad(Decimal("10"), currency="usd", fx_rate=Decimal("1.5"))
    assert result == Decimal("15.0")


def test_convert_to_cad_raises_without_a_rate_for_a_non_cad_currency():
    with pytest.raises(MissingFxRateError):
        convert_to_cad(Decimal("100"), currency="USD", fx_rate=None)


def test_price_symbol_for_crypto_ticker_uses_the_canonical_cad_pair():
    assert price_symbol_for_ticker("BTC") == "BTC-CAD"
    assert price_symbol_for_ticker("ETH") == "ETH-CAD"


def test_price_symbol_for_crypto_ticker_normalizes_case_and_whitespace():
    assert price_symbol_for_ticker(" btc ") == "BTC-CAD"


def test_price_symbol_for_equity_ticker_is_unchanged():
    assert price_symbol_for_ticker("AAPL") == "AAPL"
    assert price_symbol_for_ticker("VFV.TO") == "VFV.TO"
