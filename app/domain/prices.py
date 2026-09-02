"""Pure FX-symbol mapping + CAD conversion math (KCH-58 / AA-21).

No I/O here — `worker.prices` fetches quotes and writes `public.prices`,
this module only decides *which* yfinance symbol represents a currency's
CAD rate and does the multiplication, same domain/IO split as
`app.domain.gold`/`app.domain.buckets`.

Every non-CAD amount `app.domain.gold.value_holding` currently faces at
cost/face value (its own module docstring flags this as "the known MVP
limitation ... until AA-21 lands") is expected to route through
`convert_to_cad` once a caller (AA-22's dashboard) has both a face-value
amount and a same-day `public.prices` FX row to pair it with.
"""

from __future__ import annotations

from decimal import Decimal

CAD = "CAD"

# yfinance has no single ticker convention across asset classes: fiat pairs
# use the `"{BASE}{QUOTE}=X"` suffix, spot crypto uses `"{BASE}-{QUOTE}"`.
# One line per crypto currency code an adapter can stage (`worker.adapters
# .kraken` stages raw asset codes like "BTC"/"ETH" as `holdings.currency`),
# same one-line-per-code maintenance convention as `app.domain.buckets`.
CRYPTO_CURRENCIES: frozenset[str] = frozenset({"BTC", "ETH"})


class MissingFxRateError(ValueError):
    """A non-CAD amount was given with no FX rate to convert it — raised
    rather than silently treating it as CAD-face-value, per CLAUDE.md's
    data-provenance-first priority."""


def fx_symbol_for_currency(currency: str) -> str:
    """The `public.prices.ticker` value `worker.prices` fetches/stores this
    currency's CAD rate under. `currency == "CAD"` has no rate (1:1, never
    fetched) — callers should not call this for CAD."""
    normalized = currency.strip().upper()
    if normalized in CRYPTO_CURRENCIES:
        return f"{normalized}-CAD"
    return f"{normalized}CAD=X"


def price_symbol_for_ticker(ticker: str) -> str:
    """The yfinance symbol used to price a `holdings.ticker` value.

    `worker.adapters.kraken` stages a crypto holding's asset code as both
    `ticker` and `currency` (e.g. `ticker="BTC"`, `currency="BTC"`) — yfinance
    can't price the raw code standalone, so this routes it through the same
    crypto-to-CAD-pair convention `fx_symbol_for_currency` uses, giving
    `worker.prices.refresh_prices` one canonical `BTC-CAD` symbol instead of
    a separate, unpriceable `BTC` ticker. Equity/ETF tickers are unaffected.
    """
    normalized = ticker.strip().upper()
    if normalized in CRYPTO_CURRENCIES:
        return f"{normalized}-CAD"
    return ticker


def convert_to_cad(amount: Decimal, *, currency: str, fx_rate: Decimal | None) -> Decimal:
    """Convert `amount` (in `currency`) to CAD using `fx_rate` (units of CAD
    per one unit of `currency`, i.e. `public.prices.close` for
    `fx_symbol_for_currency(currency)`).

    CAD amounts pass through unchanged and never require a rate. Any other
    currency with `fx_rate is None` raises `MissingFxRateError` instead of
    quietly returning the face-value amount — a caller that can't source
    today's rate must decide how to handle that gap explicitly, not have it
    silently absorbed here.
    """
    normalized = currency.strip().upper()
    if normalized == CAD:
        return amount
    if fx_rate is None:
        raise MissingFxRateError(f"no FX rate available to convert {normalized} to CAD")
    return amount * fx_rate


__all__ = [
    "CAD",
    "CRYPTO_CURRENCIES",
    "MissingFxRateError",
    "convert_to_cad",
    "fx_symbol_for_currency",
    "price_symbol_for_ticker",
]
