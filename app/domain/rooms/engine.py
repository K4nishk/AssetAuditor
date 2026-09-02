"""Pure contribution-room ledger engine.

Implements the TFSA/RRSP/FHSA rules in docs/vault/20-domain/Contribution-Rooms.md.
No I/O, no wall-clock reads — `as_of_year` is an explicit argument so results are
reproducible; callers (API routes, gold rebuild) pass `date.today().year`.
"""

from decimal import Decimal

from app.domain.rooms.cra_limits import DEFAULT_LIMITS_TABLE, CraLimitsTable
from app.domain.rooms.models import (
    RoomBreakdown,
    RoomEvent,
    RoomLedgerEntry,
    RoomsResult,
    UserFacts,
)

ZERO = Decimal("0")


def compute_rooms(
    user_facts: UserFacts,
    room_events: list[RoomEvent],
    limits_table: CraLimitsTable = DEFAULT_LIMITS_TABLE,
    *,
    as_of_year: int,
) -> RoomsResult:
    """Compute TFSA/RRSP/FHSA room from user facts and a room-events ledger."""
    return RoomsResult(
        tfsa=_compute_tfsa(user_facts, room_events, limits_table, as_of_year),
        rrsp=_compute_rrsp(user_facts, room_events, limits_table, as_of_year),
        fhsa=_compute_fhsa(user_facts, room_events, limits_table, as_of_year),
    )


def _events(room_events: list[RoomEvent], account_type: str, kind: str) -> list[RoomEvent]:
    return [e for e in room_events if e.account_type == account_type and e.kind == kind]


def _latest_override(overrides: list[RoomEvent]) -> RoomEvent | None:
    if not overrides:
        return None
    return max(overrides, key=lambda e: e.year)


def _compute_tfsa(
    user_facts: UserFacts,
    room_events: list[RoomEvent],
    limits_table: CraLimitsTable,
    as_of_year: int,
) -> RoomBreakdown:
    turned_18_year = as_of_year - (user_facts.age - 18)
    start_year = max(
        limits_table.tfsa_first_eligible_year, user_facts.year_in_canada, turned_18_year
    )

    ledger: list[RoomLedgerEntry] = []
    computed_total = ZERO
    for year in range(start_year, as_of_year + 1):
        limit = limits_table.tfsa_limit_for(year)
        if limit is None:
            raise ValueError(
                f"limits table {limits_table.version} has no TFSA limit for {year}; "
                "add the published limit before computing room for this year"
            )
        computed_total += limit
        ledger.append(RoomLedgerEntry(year=year, kind="grant", amount=limit))

    tfsa_contributions = _events(room_events, "tfsa", "contribution")
    contributions = [e for e in tfsa_contributions if e.year <= as_of_year]
    withdrawals = _events(room_events, "tfsa", "withdrawal")
    overrides = _events(room_events, "tfsa", "cra_override")

    ledger.extend(
        RoomLedgerEntry(year=e.year, kind=e.kind, amount=e.amount)
        for e in contributions + withdrawals
    )

    contributed = sum((e.amount for e in contributions), ZERO)
    # Withdrawals are re-credited on Jan 1 of the year *after* the withdrawal.
    recredited = sum((w.amount for w in withdrawals if w.year + 1 <= as_of_year), ZERO)
    room_used = max(contributed - recredited, ZERO)

    override = _latest_override(overrides)
    room_total = computed_total
    if override is not None:
        delta = override.amount - computed_total
        ledger.append(
            RoomLedgerEntry(
                year=override.year,
                kind="cra_override",
                amount=override.amount,
                note=f"delta vs computed: {delta}",
            )
        )
        room_total = override.amount

    return RoomBreakdown(
        room_total=room_total,
        room_used=room_used,
        room_remaining=room_total - room_used,
        ledger=sorted(ledger, key=lambda e: e.year),
    )


def _compute_rrsp(
    user_facts: UserFacts,
    room_events: list[RoomEvent],
    limits_table: CraLimitsTable,
    as_of_year: int,
) -> RoomBreakdown:
    income = user_facts.prior_year_earned_income or ZERO
    annual_cap = limits_table.rrsp_limit_for(as_of_year)
    computed_new_room = min(income * limits_table.rrsp_room_rate, annual_cap)

    pension_adjustments = [
        e for e in _events(room_events, "rrsp", "pension_adjustment") if e.year == as_of_year
    ]
    pension_adjustment_total = sum((e.amount for e in pension_adjustments), ZERO)
    new_room = max(computed_new_room - pension_adjustment_total, ZERO)

    rrsp_grants = _events(room_events, "rrsp", "grant")
    carry_forward_grants = [e for e in rrsp_grants if e.year < as_of_year]
    carry_forward = sum((e.amount for e in carry_forward_grants), ZERO)

    rrsp_contributions = _events(room_events, "rrsp", "contribution")
    contributions = [e for e in rrsp_contributions if e.year <= as_of_year]
    overrides = _events(room_events, "rrsp", "cra_override")

    ledger: list[RoomLedgerEntry] = [
        RoomLedgerEntry(
            year=as_of_year,
            kind="grant",
            amount=new_room,
            note=f"18% of {income} capped at {annual_cap}, minus PA {pension_adjustment_total}",
        ),
        *(RoomLedgerEntry(year=e.year, kind=e.kind, amount=e.amount) for e in carry_forward_grants),
        *(RoomLedgerEntry(year=e.year, kind=e.kind, amount=e.amount) for e in pension_adjustments),
        *(RoomLedgerEntry(year=e.year, kind=e.kind, amount=e.amount) for e in contributions),
    ]

    computed_total = new_room + carry_forward
    room_used = sum((e.amount for e in contributions), ZERO)

    override = _latest_override(overrides)
    room_total = computed_total
    if override is not None:
        delta = override.amount - computed_total
        ledger.append(
            RoomLedgerEntry(
                year=override.year,
                kind="cra_override",
                amount=override.amount,
                note=f"delta vs computed: {delta}",
            )
        )
        room_total = override.amount

    return RoomBreakdown(
        room_total=room_total,
        room_used=room_used,
        room_remaining=room_total - room_used,
        ledger=sorted(ledger, key=lambda e: e.year),
    )


def fhsa_year_contribution_cap(
    year: int,
    opened_year: int,
    contributions_by_year: dict[int, Decimal],
    limits_table: CraLimitsTable = DEFAULT_LIMITS_TABLE,
) -> Decimal:
    """Max amount addable to FHSA room *in a single year*.

    Unused room from only the immediately preceding year carries forward, capped
    at one year's annual limit — so a $0 first year makes $16,000 addable the
    year after, not more.
    """
    if year < opened_year:
        return ZERO
    prior_year = year - 1
    if prior_year < opened_year:
        return limits_table.fhsa_annual_limit
    prior_contribution = contributions_by_year.get(prior_year, ZERO)
    prior_unused = max(limits_table.fhsa_annual_limit - prior_contribution, ZERO)
    carry_forward = min(prior_unused, limits_table.fhsa_carryforward_cap)
    return limits_table.fhsa_annual_limit + carry_forward


def _compute_fhsa(
    user_facts: UserFacts,
    room_events: list[RoomEvent],
    limits_table: CraLimitsTable,
    as_of_year: int,
) -> RoomBreakdown:
    if user_facts.fhsa_opened_year is None:
        return RoomBreakdown(room_total=ZERO, room_used=ZERO, room_remaining=ZERO, ledger=[])

    opened_year = user_facts.fhsa_opened_year
    last_participation_year = opened_year + limits_table.fhsa_max_participation_years - 1
    end_year = min(as_of_year, last_participation_year)

    ledger: list[RoomLedgerEntry] = []
    cumulative = ZERO
    for year in range(opened_year, end_year + 1):
        grant = min(limits_table.fhsa_annual_limit, limits_table.fhsa_lifetime_limit - cumulative)
        grant = max(grant, ZERO)
        cumulative += grant
        ledger.append(RoomLedgerEntry(year=year, kind="grant", amount=grant))

    fhsa_contributions = _events(room_events, "fhsa", "contribution")
    contributions = [e for e in fhsa_contributions if e.year <= as_of_year]
    withdrawals = _events(room_events, "fhsa", "withdrawal")
    overrides = _events(room_events, "fhsa", "cra_override")

    ledger.extend(
        RoomLedgerEntry(
            year=e.year,
            kind=e.kind,
            amount=e.amount,
            note="qualifying FHSA withdrawals do not restore room",
        )
        for e in withdrawals
    )
    ledger.extend(RoomLedgerEntry(year=e.year, kind=e.kind, amount=e.amount) for e in contributions)

    room_used = sum((e.amount for e in contributions), ZERO)

    override = _latest_override(overrides)
    room_total = cumulative
    if override is not None:
        delta = override.amount - cumulative
        ledger.append(
            RoomLedgerEntry(
                year=override.year,
                kind="cra_override",
                amount=override.amount,
                note=f"delta vs computed: {delta}",
            )
        )
        room_total = override.amount

    return RoomBreakdown(
        room_total=room_total,
        room_used=room_used,
        room_remaining=room_total - room_used,
        ledger=sorted(ledger, key=lambda e: e.year),
    )
