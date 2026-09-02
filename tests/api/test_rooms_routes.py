"""HTTP-layer tests for the rooms routes (KCH-44 / AA-9).

Follows tests/api/test_profile_routes.py's approach: `TestClient` + a fake
connection standing in for a live Postgres — the SQL wrappers themselves are
exercised against real Postgres in tests/db/test_room_events_live.py (skips
in this sandbox per CLAUDE.md's shmget/network limitations).

`app.routes.rooms` reads the wall clock for `as_of_year`
(`datetime.now(UTC).year`) — the routes' own docstring says that's
deliberate, the engine itself stays pure. Tests freeze it to 2026-07-31 so
the golden numbers from data/samples/README.md (also what
tests/unit/test_rooms_engine.py asserts) stay correct regardless of when
the suite runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import rooms as rooms_module

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001 - matches datetime.now's signature
        return datetime(2026, 7, 31, tzinfo=tz)


class FakeConnection:
    def __init__(self, *, profile_row, room_event_rows=(), override_row=None):
        self.profile_row = profile_row
        self.room_event_rows = list(room_event_rows)
        self.override_row = override_row
        self.calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if "insert into public.room_events" in query:
            return self.override_row
        return self.profile_row

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.room_event_rows


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(rooms_module._conn, None)


@pytest.fixture(autouse=True)
def _freeze_as_of_year(monkeypatch):
    monkeypatch.setattr(rooms_module, "datetime", _FrozenDatetime)


def _override_conn(**kwargs) -> FakeConnection:
    fake = FakeConnection(**kwargs)

    async def _fake_conn():
        yield fake

    app.dependency_overrides[rooms_module._conn] = _fake_conn
    return fake


def _profile_row(**overrides) -> dict:
    row = {
        "id": USER_ID,
        "age": 29,
        "holdings_country": "CA",
        "year_in_canada": 2019,
        "fhsa_opened_year": 2024,
        "risk_profile": "medium",
        "prior_year_earned_income": Decimal("82000"),
        "deactivated_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


def _room_event_row(**overrides) -> dict:
    row = {
        "id": str(uuid.uuid4()),
        "account_type": "tfsa",
        "year": 2026,
        "kind": "contribution",
        "amount": Decimal("10300"),
        "source_ref": str(uuid.uuid4()),
        "created_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


# --- GET /api/rooms --------------------------------------------------------


def test_get_rooms_reproduces_the_tfsa_golden_number():
    _override_conn(profile_row=_profile_row(), room_event_rows=[_room_event_row()])

    response = client.get("/api/rooms")

    assert response.status_code == 200
    tfsa = response.json()["tfsa"]
    assert tfsa["room_total"] == "51500"
    assert tfsa["room_used"] == "10300"
    assert tfsa["room_remaining"] == "41200"


def test_get_rooms_links_contribution_ledger_entries_to_their_source():
    row = _room_event_row()
    _override_conn(profile_row=_profile_row(), room_event_rows=[row])

    response = client.get("/api/rooms")

    ledger = response.json()["tfsa"]["ledger"]
    contribution = next(e for e in ledger if e["kind"] == "contribution")
    assert contribution["room_event_id"] == row["id"]
    assert contribution["source_ref"] == row["source_ref"]


def test_get_rooms_grant_entries_are_expandable_but_unlinked():
    _override_conn(profile_row=_profile_row(), room_event_rows=[])

    response = client.get("/api/rooms")

    ledger = response.json()["tfsa"]["ledger"]
    grants = [e for e in ledger if e["kind"] == "grant"]
    assert grants
    assert all(g["room_event_id"] is None for g in grants)


def test_get_rooms_404s_when_no_profile_exists_yet():
    _override_conn(profile_row=None)

    response = client.get("/api/rooms")

    assert response.status_code == 404


def test_get_rooms_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/rooms")

    assert response.status_code == 401


# --- POST /api/rooms/override ----------------------------------------------


def test_create_cra_override_recomputes_the_ledger_with_the_delta():
    fake = _override_conn(
        profile_row=_profile_row(),
        room_event_rows=[_room_event_row(kind="cra_override", amount=Decimal("40000"))],
        override_row=_room_event_row(kind="cra_override", amount=Decimal("40000")),
    )

    response = client.post(
        "/api/rooms/override",
        json={"account_type": "tfsa", "year": 2026, "amount": "40000"},
    )

    assert response.status_code == 200
    tfsa = response.json()["tfsa"]
    assert tfsa["room_total"] == "40000"
    override_entry = next(e for e in tfsa["ledger"] if e["kind"] == "cra_override")
    assert "delta vs computed" in override_entry["note"]

    insert_call = next(c for c in fake.calls if "insert into public.room_events" in c[0])
    assert insert_call[1] == (USER_ID, "tfsa", 2026, Decimal("40000"))


def test_create_cra_override_rejects_a_negative_amount():
    _override_conn(profile_row=_profile_row())

    response = client.post(
        "/api/rooms/override",
        json={"account_type": "tfsa", "year": 2026, "amount": "-1"},
    )

    assert response.status_code == 422


def test_create_cra_override_rejects_an_invalid_account_type():
    _override_conn(profile_row=_profile_row())

    response = client.post(
        "/api/rooms/override",
        json={"account_type": "resp", "year": 2026, "amount": "1000"},
    )

    assert response.status_code == 422


def test_create_cra_override_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.post(
        "/api/rooms/override",
        json={"account_type": "tfsa", "year": 2026, "amount": "1000"},
    )

    assert response.status_code == 401
