"""Unit tests for room-events drill-down linking (KCH-44 / AA-9).

`app.domain.rooms.drilldown.link_rooms_result` is the piece that lets the
rooms screen expand a figure to its ledger and see which `room_events` row
(and, for contributions, which source transaction) produced each line —
`app.domain.rooms.engine.compute_rooms` itself (AA-8) only knows
year/kind/amount/note, not database identity.
"""

from decimal import Decimal

from app.domain.rooms import RoomEvent, UserFacts, compute_rooms
from app.domain.rooms.drilldown import RawRoomEvent, link_rooms_result

ALEX = UserFacts(
    age=29,
    year_in_canada=2019,
    fhsa_opened_year=2024,
    prior_year_earned_income=Decimal("82000"),
)


def test_contribution_entry_links_back_to_its_source_room_event() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="contribution", amount=Decimal("10300")),
    ]
    raw = [
        RawRoomEvent(
            id="evt-1",
            account_type="tfsa",
            year=2026,
            kind="contribution",
            amount=Decimal("10300"),
            source_ref="txn-1",
        ),
    ]

    linked = link_rooms_result(compute_rooms(ALEX, events, as_of_year=2026), raw)

    contribution = next(e for e in linked.tfsa.ledger if e.kind == "contribution")
    assert contribution.room_event_id == "evt-1"
    assert contribution.source_ref == "txn-1"


def test_grant_entries_have_no_source_row() -> None:
    linked = link_rooms_result(compute_rooms(ALEX, [], as_of_year=2026), [])

    grants = [e for e in linked.tfsa.ledger if e.kind == "grant"]
    assert grants
    assert all(e.room_event_id is None and e.source_ref is None for e in grants)


def test_duplicate_year_kind_amount_events_link_in_fifo_order() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="contribution", amount=Decimal("1000")),
        RoomEvent(account_type="tfsa", year=2026, kind="contribution", amount=Decimal("1000")),
    ]
    raw = [
        RawRoomEvent(
            id="first",
            account_type="tfsa",
            year=2026,
            kind="contribution",
            amount=Decimal("1000"),
            source_ref="txn-a",
        ),
        RawRoomEvent(
            id="second",
            account_type="tfsa",
            year=2026,
            kind="contribution",
            amount=Decimal("1000"),
            source_ref="txn-b",
        ),
    ]

    linked = link_rooms_result(compute_rooms(ALEX, events, as_of_year=2026), raw)

    contributions = [e for e in linked.tfsa.ledger if e.kind == "contribution"]
    assert [c.room_event_id for c in contributions] == ["first", "second"]


def test_cra_override_links_to_its_room_event_and_keeps_the_engines_delta_note() -> None:
    events = [
        RoomEvent(account_type="tfsa", year=2026, kind="cra_override", amount=Decimal("40000")),
    ]
    raw = [
        RawRoomEvent(
            id="override-1",
            account_type="tfsa",
            year=2026,
            kind="cra_override",
            amount=Decimal("40000"),
            source_ref=None,
        ),
    ]

    linked = link_rooms_result(compute_rooms(ALEX, events, as_of_year=2026), raw)

    override = next(e for e in linked.tfsa.ledger if e.kind == "cra_override")
    assert override.room_event_id == "override-1"
    assert override.source_ref is None
    assert "delta vs computed" in override.note


def test_events_from_other_account_types_do_not_cross_link() -> None:
    events = [
        RoomEvent(
            account_type="rrsp", year=2026, kind="pension_adjustment", amount=Decimal("4100")
        ),
    ]
    raw = [
        RawRoomEvent(
            id="tfsa-row",
            account_type="tfsa",
            year=2026,
            kind="pension_adjustment",
            amount=Decimal("4100"),
            source_ref=None,
        ),
    ]

    linked = link_rooms_result(compute_rooms(ALEX, events, as_of_year=2026), raw)

    pension_entry = next(e for e in linked.rrsp.ledger if e.kind == "pension_adjustment")
    assert pension_entry.room_event_id is None
