"""HTTP-layer tests for the profile routes (KCH-42 / AA-7).

Follows tests/api/test_manual_entry_routes.py's approach: exercise routing,
status codes, and response shaping via `TestClient` + a fake connection
standing in for a live Postgres — the SQL wrapper itself is exercised
against real Postgres in tests/db/test_users_profile_live.py (skips here per
CLAUDE.md's sandbox limitations).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import profile as profile_module

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class FakeConnection:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self._response


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(profile_module._conn, None)


def _override_conn(response):
    fake = FakeConnection(response)

    async def _fake_conn():
        yield fake

    app.dependency_overrides[profile_module._conn] = _fake_conn
    return fake


def _row(**overrides) -> dict:
    row = {
        "id": USER_ID,
        "age": 35,
        "holdings_country": "CA",
        "year_in_canada": 2009,
        "fhsa_opened_year": 2024,
        "risk_profile": "medium",
        "prior_year_earned_income": "85000.00",
        "deactivated_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    row.update(overrides)
    return row


# --- GET /api/profile ---------------------------------------------------


def test_get_profile_returns_the_row_with_the_room_widgets_flag():
    _override_conn(_row())

    response = client.get("/api/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["age"] == 35
    assert body["shows_room_widgets"] is True


def test_get_profile_hides_room_widgets_for_a_non_ca_country():
    _override_conn(_row(holdings_country="US"))

    response = client.get("/api/profile")

    assert response.status_code == 200
    assert response.json()["shows_room_widgets"] is False


def test_get_profile_404s_when_no_row_exists_yet():
    _override_conn(None)

    response = client.get("/api/profile")

    assert response.status_code == 404


def test_get_profile_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/profile")

    assert response.status_code == 401


# --- PUT /api/profile ----------------------------------------------------

_VALID_BODY = {
    "age": 35,
    "holdings_country": "CA",
    "year_in_canada": 2009,
    "fhsa_opened_year": 2024,
    "risk_profile": "medium",
    "prior_year_earned_income": "85000.00",
}


def test_upsert_profile_creates_or_replaces_and_returns_the_row():
    fake = _override_conn(_row())

    response = client.put("/api/profile", json=_VALID_BODY)

    assert response.status_code == 200
    assert response.json()["risk_profile"] == "medium"
    assert fake.calls[0][1][0] == USER_ID


def test_upsert_profile_409s_when_the_row_is_deactivated():
    _override_conn(None)

    response = client.put("/api/profile", json=_VALID_BODY)

    assert response.status_code == 409


def test_upsert_profile_rejects_an_invalid_risk_profile():
    _override_conn(_row())

    response = client.put("/api/profile", json={**_VALID_BODY, "risk_profile": "yolo"})

    assert response.status_code == 422


def test_upsert_profile_rejects_a_non_two_letter_country_code():
    _override_conn(_row())

    response = client.put("/api/profile", json={**_VALID_BODY, "holdings_country": "Canada"})

    assert response.status_code == 422


def test_upsert_profile_rejects_an_out_of_range_age():
    _override_conn(_row())

    response = client.put("/api/profile", json={**_VALID_BODY, "age": 200})

    assert response.status_code == 422


def test_upsert_profile_allows_omitting_optional_fhsa_and_income():
    fake = _override_conn(_row(fhsa_opened_year=None, prior_year_earned_income=None))

    omitted = ("fhsa_opened_year", "prior_year_earned_income")
    body = {k: v for k, v in _VALID_BODY.items() if k not in omitted}
    response = client.put("/api/profile", json=body)

    assert response.status_code == 200
    assert fake.calls[0][1][4] is None  # fhsa_opened_year
    assert fake.calls[0][1][6] is None  # prior_year_earned_income


def test_upsert_profile_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.put("/api/profile", json=_VALID_BODY)

    assert response.status_code == 401
