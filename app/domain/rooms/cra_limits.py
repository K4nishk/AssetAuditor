"""Versioned CRA TFSA/RRSP/FHSA limits table.

Source: docs/vault/20-domain/Contribution-Rooms.md (verified 2026-08). CRA
publishes new dollar limits annually — bump `CURRENT_VERSION` and add the new
year's entries below; the engine reads whatever `DEFAULT_LIMITS_TABLE` points
at, so callers pinned to an older table (e.g. re-running a prior tax year)
can pass their own `CraLimitsTable` instead.
"""

from dataclasses import dataclass, field
from decimal import Decimal

TFSA_FIRST_ELIGIBLE_YEAR = 2009

TFSA_ANNUAL_LIMITS: dict[int, Decimal] = {
    2009: Decimal("5000"),
    2010: Decimal("5000"),
    2011: Decimal("5000"),
    2012: Decimal("5000"),
    2013: Decimal("5500"),
    2014: Decimal("5500"),
    2015: Decimal("10000"),
    2016: Decimal("5500"),
    2017: Decimal("5500"),
    2018: Decimal("5500"),
    2019: Decimal("6000"),
    2020: Decimal("6000"),
    2021: Decimal("6000"),
    2022: Decimal("6000"),
    2023: Decimal("6500"),
    2024: Decimal("7000"),
    2025: Decimal("7000"),
    2026: Decimal("7000"),
}

RRSP_ANNUAL_LIMITS: dict[int, Decimal] = {
    2025: Decimal("32490"),
    2026: Decimal("33810"),
}
RRSP_ROOM_RATE = Decimal("0.18")

FHSA_ANNUAL_LIMIT = Decimal("8000")
FHSA_LIFETIME_LIMIT = Decimal("40000")
# Unused room from the immediately preceding year only; caps the amount
# addable to room in any single year at 2x the annual limit ($16,000).
FHSA_CARRYFORWARD_CAP = Decimal("8000")
FHSA_MAX_PARTICIPATION_YEARS = 15


@dataclass(frozen=True)
class CraLimitsTable:
    """A versioned snapshot of the CRA limits the engine computes against."""

    version: str
    tfsa_annual_limits: dict[int, Decimal] = field(default_factory=dict)
    rrsp_annual_limits: dict[int, Decimal] = field(default_factory=dict)
    rrsp_room_rate: Decimal = RRSP_ROOM_RATE
    fhsa_annual_limit: Decimal = FHSA_ANNUAL_LIMIT
    fhsa_lifetime_limit: Decimal = FHSA_LIFETIME_LIMIT
    fhsa_carryforward_cap: Decimal = FHSA_CARRYFORWARD_CAP
    fhsa_max_participation_years: int = FHSA_MAX_PARTICIPATION_YEARS
    tfsa_first_eligible_year: int = TFSA_FIRST_ELIGIBLE_YEAR

    def tfsa_limit_for(self, year: int) -> Decimal | None:
        return self.tfsa_annual_limits.get(year)

    def rrsp_limit_for(self, year: int) -> Decimal:
        if year in self.rrsp_annual_limits:
            return self.rrsp_annual_limits[year]
        latest_known_year = max(self.rrsp_annual_limits)
        return self.rrsp_annual_limits[latest_known_year]


CURRENT_VERSION = "2026"

DEFAULT_LIMITS_TABLE = CraLimitsTable(
    version=CURRENT_VERSION,
    tfsa_annual_limits=TFSA_ANNUAL_LIMITS,
    rrsp_annual_limits=RRSP_ANNUAL_LIMITS,
)
