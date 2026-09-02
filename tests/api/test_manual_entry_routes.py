"""HTTP-layer tests for the manual-entry routes (KCH-55 / AA-20).

Follows tests/api/test_uploads.py's approach: exercise routing, status codes,
and the bronze/job/staged-row write sequence via `TestClient` + dependency
overrides, with a fake connection/blob client standing in for a live
Postgres/Vercel Blob — the draft-building logic itself is covered by
tests/unit/test_manual_entry_domain.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import manual_entry as manual_entry_module
from app.uploads.blob import BlobUploadError

USER_ID = "00000000-0000-0000-0000-000000000042"

client = TestClient(app)


class FakeConnection:
    """Returns each entry of `responses` in order, one per `fetchrow` call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    async def fetchrow(self, query, *args):
        self.call_count += 1
        return self._responses.pop(0)


class FakeBlobStorage:
    def __init__(self, url="https://blob.example/bronze/u/abc", error=None):
        self.url = url
        self.error = error
        self.calls = []

    def put(self, pathname, data, content_type):
        self.calls.append((pathname, data, content_type))
        if self.error is not None:
            raise self.error
        return self.url


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


def _override_conn(responses):
    async def _fake_conn():
        yield FakeConnection(responses)

    app.dependency_overrides[manual_entry_module._conn] = _fake_conn


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(manual_entry_module._conn, None)


def _happy_responses(n_drafts: int) -> list[dict]:
    return [
        {"id": "bronze-1"},  # insert_bronze_file
        {"id": "job-1", "status": "needs_user"},  # insert_needs_user_job
        {"id": "lineage-start"},  # LineageEmitter.start
        *({"id": f"row-{i}"} for i in range(n_drafts)),  # staged_rows.insert_draft
        {"id": "lineage-complete"},  # LineageEmitter.complete
    ]


def _account(**overrides) -> dict:
    account = {
        "institution": "Questrade",
        "account_type": "TFSA",
        "account_number": "1234",
        "currency": "CAD",
    }
    account.update(overrides)
    return account


# --- POST /api/manual-entry/portfolio ----------------------------------------


def test_submit_portfolio_entry_stages_rows_and_returns_the_job(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(manual_entry_module, "get_blob_storage", lambda: fake_blob)
    _override_conn(_happy_responses(2))  # account + holding

    response = client.post(
        "/api/manual-entry/portfolio",
        json={
            "account": _account(),
            "ticker": "aapl",
            "quantity": "10",
            "avg_cost": "150.00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"job_id": "job-1", "status": "needs_user", "row_count": 2}
    assert fake_blob.calls[0][0].startswith(f"bronze/{USER_ID}/")
    assert fake_blob.calls[0][2] == "application/json"


def test_submit_portfolio_entry_rejects_missing_avg_cost_and_lots():
    _override_conn([])

    response = client.post(
        "/api/manual-entry/portfolio",
        json={"account": _account(), "ticker": "AAPL", "quantity": "10"},
    )

    assert response.status_code == 422
    assert "avg_cost or" in response.json()["detail"]


def test_submit_portfolio_entry_rejects_an_unmaskable_account_number():
    _override_conn([])

    response = client.post(
        "/api/manual-entry/portfolio",
        json={
            "account": _account(account_number="not-a-number"),
            "ticker": "AAPL",
            "quantity": "10",
            "avg_cost": "1",
        },
    )

    assert response.status_code == 422


def test_submit_portfolio_entry_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.post(
        "/api/manual-entry/portfolio",
        json={"account": _account(), "ticker": "AAPL", "quantity": "10", "avg_cost": "1"},
    )

    assert response.status_code == 401


def test_submit_portfolio_entry_returns_502_when_blob_storage_fails(monkeypatch):
    fake_blob = FakeBlobStorage(error=BlobUploadError("boom"))
    monkeypatch.setattr(manual_entry_module, "get_blob_storage", lambda: fake_blob)
    _override_conn([])

    response = client.post(
        "/api/manual-entry/portfolio",
        json={"account": _account(), "ticker": "AAPL", "quantity": "10", "avg_cost": "1"},
    )

    assert response.status_code == 502


def test_submit_portfolio_entry_reuses_the_existing_bronze_row_on_conflict(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(manual_entry_module, "get_blob_storage", lambda: fake_blob)
    _override_conn(
        [
            None,  # insert_bronze_file lost the race
            {"id": "bronze-existing"},  # find_by_sha256 fallback
            {"id": "job-1", "status": "needs_user"},  # insert_needs_user_job
            {"id": "lineage-start"},
            {"id": "row-1"},
            {"id": "row-2"},
            {"id": "lineage-complete"},
        ]
    )

    response = client.post(
        "/api/manual-entry/portfolio",
        json={"account": _account(), "ticker": "AAPL", "quantity": "10", "avg_cost": "1"},
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == "job-1"


# --- POST /api/manual-entry/portfolio/yahoo-import ---------------------------

_YAHOO_CSV = (
    "Symbol,Current Price,Date,Time,Change,Open,High,Low,Volume,"
    "Trade Date,Purchase Price,Quantity,Commission,High Limit,Low Limit,Comment\n"
    "AAPL,190.00,9/1/2026,4:00pm,+1.20,188.00,191.00,187.50,50000000,3/14/2024,171.20,4,4.95,,,\n"
    "VFV.TO,130.00,9/1/2026,4:00pm,+0.50,129.00,131.00,128.50,1000000,6/2/2024,118.40,30,0.00,,,\n"
)


def test_import_yahoo_finance_portfolio_stages_a_holding_and_lot_per_ticker(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(manual_entry_module, "get_blob_storage", lambda: fake_blob)
    # account + 2 holdings + 2 lots = 5 drafts
    _override_conn(_happy_responses(5))

    response = client.post(
        "/api/manual-entry/portfolio/yahoo-import",
        json={"account": _account(), "csv_text": _YAHOO_CSV, "currency": "USD"},
    )

    assert response.status_code == 200
    assert response.json()["row_count"] == 5


def test_import_yahoo_finance_portfolio_rejects_malformed_csv():
    _override_conn([])

    response = client.post(
        "/api/manual-entry/portfolio/yahoo-import",
        json={"account": _account(), "csv_text": "not,a,yahoo,export\n1,2,3,4\n"},
    )

    assert response.status_code == 422


# --- POST /api/manual-entry/account-balance -----------------------------------


def test_submit_account_balance_stages_account_and_transaction(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(manual_entry_module, "get_blob_storage", lambda: fake_blob)
    _override_conn(_happy_responses(2))

    response = client.post(
        "/api/manual-entry/account-balance",
        json={"account": _account(institution="Scotiabank"), "balance": "8500.00"},
    )

    assert response.status_code == 200
    assert response.json() == {"job_id": "job-1", "status": "needs_user", "row_count": 2}


def test_submit_account_balance_rejects_a_zero_balance():
    _override_conn([])

    response = client.post(
        "/api/manual-entry/account-balance",
        json={"account": _account(), "balance": "0"},
    )

    assert response.status_code == 422
