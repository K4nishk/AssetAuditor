"""Pure FX-reconciliation + cut-switcher constants (KCH-59 / AA-22)."""

from decimal import Decimal

import pytest

from app.domain.dashboard import AVAILABLE_CUTS, DEFAULT_CUT, reconcile_assets_to_cad
from app.domain.gold import AssetValuation
from app.domain.prices import MissingFxRateError


def _asset(**overrides) -> AssetValuation:
    row = {
        "account_type": "tfsa",
        "institution": "questrade",
        "currency": "CAD",
        "amount_cad": Decimal("100"),
    }
    row.update(overrides)
    return AssetValuation(**row)


def test_cad_assets_pass_through_unchanged() -> None:
    asset = _asset(currency="CAD", amount_cad=Decimal("4200"))
    [reconciled] = reconcile_assets_to_cad([asset], fx_rates={})
    assert reconciled == asset


def test_non_cad_asset_converted_using_matching_fx_rate() -> None:
    asset = _asset(currency="USD", amount_cad=Decimal("1000"))
    [reconciled] = reconcile_assets_to_cad([asset], fx_rates={"USD": Decimal("1.35")})
    assert reconciled.amount_cad == Decimal("1350.00")
    # currency/account_type/institution carry through untouched
    assert reconciled.currency == "USD"
    assert reconciled.account_type == asset.account_type
    assert reconciled.institution == asset.institution


def test_fx_lookup_is_case_insensitive() -> None:
    asset = _asset(currency="usd", amount_cad=Decimal("100"))
    [reconciled] = reconcile_assets_to_cad([asset], fx_rates={"USD": Decimal("1.5")})
    assert reconciled.amount_cad == Decimal("150.0")


def test_missing_fx_rate_raises_rather_than_silently_summing_face_value() -> None:
    asset = _asset(currency="BTC", amount_cad=Decimal("1"))
    with pytest.raises(MissingFxRateError):
        reconcile_assets_to_cad([asset], fx_rates={})


def test_mixed_currency_batch_only_converts_what_needs_it() -> None:
    assets = [
        _asset(currency="CAD", amount_cad=Decimal("500")),
        _asset(currency="USD", amount_cad=Decimal("200")),
    ]
    reconciled = reconcile_assets_to_cad(assets, fx_rates={"USD": Decimal("1.4")})
    amounts = {a.currency: a.amount_cad for a in reconciled}
    assert amounts["CAD"] == Decimal("500")
    assert amounts["USD"] == Decimal("280.0")


def test_available_cuts_matches_gold_diversification_dimensions() -> None:
    assert AVAILABLE_CUTS == ("institution", "account_type", "currency")
    assert DEFAULT_CUT in AVAILABLE_CUTS
