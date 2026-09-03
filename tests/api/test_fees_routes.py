"""HTTP-layer tests for the fee-drag route (KCH-63 / AA-26).

Same fake-connection approach as tests/api/test_diversification_routes.py:
`app.db.queries.fees.fetch_mer_by_ticker` is exercised against real Postgres
in tests/db/test_fees_query_live.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.domain.fee_drag import BENCHMARK_MER_PCT
from app.main import app
from app.routes import fees as fees_module

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class FakeConnection:
    def __init__(self, *, mer_rows=(), holding_rows=(), lot_rows=(), cash_rows=(), price_rows=None):
        self.mer_rows = list(mer_rows)
        self.holding_rows = list(holding_rows)
        self.lot_rows = list(lot_rows)
        self.cash_rows = list(cash_rows)
        self.price_rows = price_rows or {}
        self.calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if "public.prices" in query:
            return self.price_rows.get(args[0])
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if "public.holdings h" in query and "mer_pct" in query:
            return self.mer_rows
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
    app.dependency_overrides.pop(fees_module._conn, None)


def _override_conn(**kwargs) -> FakeConnection:
    fake = FakeConnection(**kwargs)

    async def _fake_conn():
        yield fake

    app.dependency_overrides[fees_module._conn] = _fake_conn
    return fake


def test_get_fee_drag_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/fees/drag")

    assert response.status_code == 401


def test_get_fee_drag_returns_empty_rows_when_no_holding_has_a_disclosed_mer():
    _override_conn(mer_rows=[])

    response = client.get("/api/fees/drag")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"] == []
    assert body["benchmark_mer_pct"] == str(BENCHMARK_MER_PCT)
    assert body["total_annual_cost_cad"] == "0"


def test_get_fee_drag_pairs_mer_with_cad_market_value():
    _override_conn(
        mer_rows=[{"ticker": "TD-BALANCED-GROWTH", "mer_pct": Decimal("2.18")}],
        holding_rows=[
            {
                "id": "h1",
                "ticker": "TD-BALANCED-GROWTH",
                "quantity": Decimal("500"),
                "avg_cost": Decimal("14.40"),
                "currency": "CAD",
                "institution": "td",
            }
        ],
    )

    response = client.get("/api/fees/drag")

    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["ticker"] == "TD-BALANCED-GROWTH"
    assert row["mer_pct"] == "2.18"
    expected_annual_cost = Decimal("500") * Decimal("14.40") * Decimal("2.18") / Decimal("100")
    assert Decimal(row["annual_cost_cad"]) == expected_annual_cost


def test_get_fee_drag_excludes_holdings_with_no_disclosed_mer():
    _override_conn(
        mer_rows=[{"ticker": "TD-BALANCED-GROWTH", "mer_pct": Decimal("2.18")}],
        holding_rows=[
            {
                "id": "h1",
                "ticker": "TD-BALANCED-GROWTH",
                "quantity": Decimal("500"),
                "avg_cost": Decimal("14.40"),
                "currency": "CAD",
                "institution": "td",
            },
            {
                "id": "h2",
                "ticker": "AAPL",
                "quantity": Decimal("10"),
                "avg_cost": Decimal("180"),
                "currency": "CAD",
                "institution": "questrade",
            },
        ],
    )

    response = client.get("/api/fees/drag")

    body = response.json()
    assert [row["ticker"] for row in body["rows"]] == ["TD-BALANCED-GROWTH"]


def test_get_fee_drag_503s_when_fx_rate_missing():
    _override_conn(
        mer_rows=[{"ticker": "AAPL", "mer_pct": Decimal("0.75")}],
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

    response = client.get("/api/fees/drag")

    assert response.status_code == 503
