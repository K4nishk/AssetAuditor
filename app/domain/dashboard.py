"""Dashboard KPI/pie shaping + FX reconciliation (KCH-59 / AA-22).

Pure math only, same domain/IO split as `app.domain.gold`/`app.domain.prices`.
`app.domain.gold`'s own module docstring documents every asset there as
face-value in its native currency until AA-21's price layer closes the gap —
"that reconciliation is AA-22's dashboard concern". `reconcile_assets_to_cad`
is that seam: `worker.gold.rebuild_gold` calls it right before
`compute_gold_totals` so every gold table (`networth_snapshots`/
`term_buckets`/`diversification_cuts`) already stores a true CAD amount —
dashboard reads never re-derive it, per CLAUDE.md's provenance rule that a
dashboard number must drill down to its source row unchanged.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.gold import AssetValuation
from app.domain.prices import CAD, convert_to_cad

# Diversification cut dimensions the dashboard's pie switcher offers — must
# match the `cut` values `app.domain.gold.compute_gold_totals` actually
# emits (`("institution", ...)`, `("account_type", ...)`, `("currency", ...)`).
AVAILABLE_CUTS: tuple[str, ...] = ("institution", "account_type", "currency")
DEFAULT_CUT = "institution"


def reconcile_assets_to_cad(
    assets: list[AssetValuation], *, fx_rates: dict[str, Decimal]
) -> list[AssetValuation]:
    """Replace each non-CAD asset's face-value amount with its true CAD value.

    `fx_rates` maps an uppercased currency code to units of CAD per one unit
    of that currency (a `public.prices` FX row, keyed the same way
    `app.domain.prices.fx_symbol_for_currency` names it). A non-CAD asset
    with no matching rate raises `MissingFxRateError` (via `convert_to_cad`)
    rather than being silently summed into a CAD total at face value —
    CLAUDE.md's data-provenance-first priority over a rebuild that always
    quietly succeeds.
    """
    reconciled = []
    for asset in assets:
        currency = asset.currency.strip().upper()
        if currency == CAD:
            reconciled.append(asset)
            continue
        reconciled.append(
            AssetValuation(
                account_type=asset.account_type,
                institution=asset.institution,
                currency=asset.currency,
                amount_cad=convert_to_cad(
                    asset.amount_cad, currency=currency, fx_rate=fx_rates.get(currency)
                ),
            )
        )
    return reconciled


__all__ = ["AVAILABLE_CUTS", "DEFAULT_CUT", "reconcile_assets_to_cad"]
