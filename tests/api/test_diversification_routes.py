"""HTTP-layer tests for the diversification flags route (KCH-61 / AA-24).

Same fake-connection approach as tests/api/test_dashboard_routes.py: the SQL
wrappers themselves are exercised against real Postgres in
tests/db/test_diversification_flags_live.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import diversification as diversification_module

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class FakeConnection:
    def __init__(
        self, *, profile_row, holding_rows=(), lot_rows=(), cash_rows=(), price_rows=None
    ):
        self.profile_row = profile_row
        self.holding_rows = list(holding_rows)
        self.lot_rows = list(lot_rows)
        self.cash_rows = list(cash_rows)
        self.price_rows = price_rows or {}
        self.calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if "public.users_profile" in query:
            return self.profile_row
        if "public.prices" in query:
            return self.price_rows.get(args[0])
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if "public.holdings" in query:
            return self.holding_rows
        if "public.lots" in query:
            return self.lot_rows
        if "public.transactions" in query:
            return self.cash_rows
        raise AssertionError(f"unexpected fetch: {query}")


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(diversification_module._conn, None)


def _override_conn(**kwargs) -> FakeConnection:
    fake = FakeConnection(**kwargs)

    async def _fake_conn():
        yield fake

    app.dependency_overrides[diversification_module._conn] = _fake_conn
    return fake


def _profile_row(**overrides) -> dict:
    row = {"id": USER_ID, "risk_profile": "medium"}
    row.update(overrides)
    return row


def test_get_diversification_flags_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/diversification/flags")

    assert response.status_code == 401


def test_get_diversification_flags_404s_when_no_profile_yet():
    _override_conn(profile_row=None)

    response = client.get("/api/diversification/flags")

    assert response.status_code == 404


def test_get_diversification_flags_computes_sector_and_home_bias_from_look_through():
    _override_conn(
        profile_row=_profile_row(risk_profile="medium"),
        holding_rows=[
            {
                "id": "h1",
                "ticker": "XIC.TO",
                "quantity": Decimal("100"),
                "avg_cost": Decimal("30"),
                "currency": "CAD",
                "institution": "questrade",
            }
        ],
        cash_rows=[
            {
                "account_id": "a1",
                "institution": "scotia",
                "kind": "credit",
                "amount": Decimal("4200"),
                "currency": "CAD",
            }
        ],
    )

    response = client.get("/api/diversification/flags")

    assert response.status_code == 200
    body = response.json()
    assert body["risk_profile"] == "medium"

    home_bias = next(f for f in body["flags"] if f["kind"] == "home_bias")
    # 100 * 30 = 3000 CAD in XIC (100% Canada look-through) / 7200 total CAD.
    assert Decimal(home_bias["weight_pct"]) == Decimal("3000") / Decimal("7200") * 100
    assert home_bias["is_triggered"] is True

    financials = next(
        f
        for f in body["flags"]
        if f["kind"] == "sector_concentration" and f["label"] == "financials"
    )
    assert Decimal(financials["weight_pct"]) > Decimal("0")

    assert not any(f["kind"] == "employer_concentration" for f in body["flags"])


def test_get_diversification_flags_503s_when_fx_rate_missing():
    _override_conn(
        profile_row=_profile_row(),
        holding_rows=[
            {
                "id": "h1",
                "ticker": "AAPL",
                "quantity": Decimal("10"),
                "avg_cost": Decimal("180"),
                "currency": "USD",
                "institution": "questrade",
            }
        ],
        price_rows={},
    )

    response = client.get("/api/diversification/flags")

    assert response.status_code == 503
