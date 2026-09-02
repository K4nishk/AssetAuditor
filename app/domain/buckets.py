"""Term-bucket (liquidity) classification rules (KCH-53 / AA-18).

Rules from `docs/vault/20-domain/Risk-Profiles.md`'s "Term-bucket pie"
section — profile-independent, purely liquidity-based:

    Short-term (<1y):  chequing, savings, HISA, cash
    Medium (1-5y):     bonds, GICs, balanced funds, FHSA earmarked for a purchase
    Long (5y+):        equities, ETFs, RPP/CPP, unvested ESOP, real estate
    Liabilities:       LOC balance, credit cards, mortgage

Classification here is keyed by silver `accounts.account_type` (the raw
string each adapter stages, e.g. "tfsa", "fhsa_invest", "mutual_fund"), not
by individual holding/ticker: `holdings`/`lots` (migration 0001) carry no
asset-class column, so a per-ticker rule (distinguishing a bond ETF from an
equity ETF inside the same account) is a refinement left for AA-24's ETF
classification table.
"""

from __future__ import annotations

from typing import Literal

TermBucket = Literal["short_term", "medium_term", "long_term"]

LIABILITY_BUCKET: Literal["liabilities"] = "liabilities"

# One line per new institution/adapter's account_type, same maintenance
# convention as app/domain/rooms/cra_limits.py's versioned limits table.
_SHORT_TERM: frozenset[str] = frozenset({"chequing", "savings", "hisa", "cash"})
_MEDIUM_TERM: frozenset[str] = frozenset({"fhsa", "fhsa_invest", "mutual_fund"})
_LONG_TERM: frozenset[str] = frozenset(
    {"tfsa", "rrsp", "rpp", "esop", "crypto_exchange", "crypto_brokerage"}
)


class UnknownAccountType(ValueError):
    """No term-bucket mapping exists yet for this `account_type`.

    Raised rather than silently dropping (or misclassifying) money from the
    term-bucket pie, per CLAUDE.md's data-provenance-first priority. Extend
    the sets above one line at a time as new institutions/adapters land.
    """


def classify_term_bucket(account_type: str) -> TermBucket:
    normalized = account_type.strip().lower()
    if normalized in _SHORT_TERM:
        return "short_term"
    if normalized in _MEDIUM_TERM:
        return "medium_term"
    if normalized in _LONG_TERM:
        return "long_term"
    raise UnknownAccountType(f"no term-bucket mapping for account_type={account_type!r}")
