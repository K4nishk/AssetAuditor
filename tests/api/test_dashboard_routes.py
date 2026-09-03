"""HTTP-layer tests for the dashboard route (KCH-59 / AA-22).

Same fake-connection approach as tests/api/test_rooms_routes.py: the SQL
wrappers themselves are exercised against real Postgres in
tests/db/test_dashboard_queries_live.py.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import dashboard as dashboard_module

USER_ID = "00000000-0000-0000-0000-000000000042"
RUN_ID = str(uuid.uuid4())

client = TestClient(app)


class FakeConnection:
    def __init__(
        self, *, snapshot_row=None, term_bucket_rows=(), diversification_rows=(), history_rows=()
    ):
        self.snapshot_row = snapshot_row
        self.term_bucket_rows = list(term_bucket_rows)
        self.diversification_rows = list(diversification_rows)
        self.history_rows = list(history_rows)
        self.calls: list[tuple] = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.snapshot_row

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        if "public.term_buckets" in query:
            return self.term_bucket_rows
        if "public.diversification_cuts" in query:
            return self.diversification_rows
        if "public.networth_snapshots" in query:
            return self.history_rows
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(dashboard_module._conn, None)


def _override_conn(**kwargs) -> FakeConnection:
    fake = FakeConnection(**kwargs)

    async def _fake_conn():
        yield fake

    app.dependency_overrides[dashboard_module._conn] = _fake_conn
    return fake


def _snapshot_row(**overrides) -> dict:
    row = {
        "snapshot_date": date(2026, 7, 31),
        "total_assets_cad": Decimal("618259.00"),
        "total_liabilities_cad": Decimal("421800.00"),
        "net_worth_cad": Decimal("196459.00"),
        "run_id": RUN_ID,
    }
    row.update(overrides)
    return row


def test_get_dashboard_returns_kpis_and_term_buckets():
    _override_conn(
        snapshot_row=_snapshot_row(),
        term_bucket_rows=[
            {"bucket": "short_term", "amount_cad": Decimal("27700.00"), "run_id": RUN_ID},
            {"bucket": "liabilities", "amount_cad": Decimal("421800.00"), "run_id": RUN_ID},
        ],
    )

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == "2026-07-31"
    assert body["kpis"]["net_worth_cad"] == "196459.00"
    assert body["kpis"]["total_assets_cad"] == "618259.00"
    assert {b["bucket"] for b in body["term_buckets"]} == {"short_term", "liabilities"}


def test_get_dashboard_defaults_diversification_cut_to_institution():
    _override_conn(
        snapshot_row=_snapshot_row(),
        diversification_rows=[
            {"label": "questrade", "amount_cad": Decimal("16150.00"), "run_id": RUN_ID},
        ],
    )

    response = client.get("/api/dashboard")

    body = response.json()
    assert body["diversification_cut"] == "institution"
    assert body["available_cuts"] == ["institution", "account_type", "currency"]
    assert body["diversification"][0]["label"] == "questrade"


def test_get_dashboard_switches_diversification_cut():
    fake = _override_conn(
        snapshot_row=_snapshot_row(),
        diversification_rows=[
            {"label": "USD", "amount_cad": Decimal("16150.00"), "run_id": RUN_ID},
        ],
    )

    response = client.get("/api/dashboard?cut=currency")

    assert response.status_code == 200
    assert response.json()["diversification_cut"] == "currency"
    cuts_call = next(c for c in fake.calls if "public.diversification_cuts" in c[0])
    assert cuts_call[1] == (USER_ID, date(2026, 7, 31), "currency")


def test_get_dashboard_rejects_unknown_cut():
    _override_conn(snapshot_row=_snapshot_row())

    response = client.get("/api/dashboard?cut=sector")

    assert response.status_code == 422


def test_get_dashboard_404s_when_no_snapshot_exists_yet():
    _override_conn(snapshot_row=None)

    response = client.get("/api/dashboard")

    assert response.status_code == 404


def test_get_dashboard_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/dashboard")

    assert response.status_code == 401


def test_get_networth_history_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.get("/api/dashboard/history")

    assert response.status_code == 401


def test_get_networth_history_returns_empty_points_when_no_snapshots_exist():
    _override_conn(history_rows=[])

    response = client.get("/api/dashboard/history")

    assert response.status_code == 200
    assert response.json() == {"points": []}


def test_get_networth_history_returns_points_oldest_first():
    fake = _override_conn(
        history_rows=[
            {
                "snapshot_date": date(2026, 6, 30),
                "total_assets_cad": Decimal("600000.00"),
                "total_liabilities_cad": Decimal("425000.00"),
                "net_worth_cad": Decimal("175000.00"),
            },
            {
                "snapshot_date": date(2026, 7, 31),
                "total_assets_cad": Decimal("618259.00"),
                "total_liabilities_cad": Decimal("421800.00"),
                "net_worth_cad": Decimal("196459.00"),
            },
        ]
    )

    response = client.get("/api/dashboard/history")

    assert response.status_code == 200
    body = response.json()
    assert [p["snapshot_date"] for p in body["points"]] == ["2026-06-30", "2026-07-31"]
    assert body["points"][1]["net_worth_cad"] == "196459.00"
    history_call = next(c for c in fake.calls if "public.networth_snapshots" in c[0])
    assert "order by snapshot_date asc" in history_call[0]
