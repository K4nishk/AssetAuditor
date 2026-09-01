"""HTTP-layer tests for the upload routes (KCH-46 / AA-11).

Follows tests/api/test_auth_dependency.py's approach: exercise the routes the
way a real request hits them (TestClient, dependency overrides for auth + the
DB connection, a fake in place of the real Vercel Blob client), without a
live Postgres or network. The SQL wrappers themselves are covered by their
own unit tests; this file only checks routing, status codes, and the
token/sha256/content-type checks that gate whether a write happens at all.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.main import app
from app.routes import uploads as uploads_module
from app.uploads.blob import BlobUploadError
from app.uploads.signing import create_upload_token

USER_ID = "00000000-0000-0000-0000-000000000042"
SECRET = b"test-upload-secret"


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
def _env(monkeypatch):
    monkeypatch.setenv("UPLOAD_TOKEN_SECRET", SECRET.decode())


@pytest.fixture(autouse=True)
def _auth_override():
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


def _override_conn(responses):
    async def _fake_conn():
        yield FakeConnection(responses)

    app.dependency_overrides[uploads_module._conn] = _fake_conn


@pytest.fixture(autouse=True)
def _clear_conn_override():
    yield
    app.dependency_overrides.pop(uploads_module._conn, None)


client = TestClient(app)


# --- POST /api/uploads -------------------------------------------------------


def test_register_upload_returns_a_signed_url_for_a_new_file():
    _override_conn([None])  # find_by_sha256 -> no existing row

    response = client.post(
        "/api/uploads",
        json={
            "sha256_hex": "a" * 64,
            "size_bytes": 1024,
            "content_type": "application/pdf",
            "institution": "scotiabank",
            "period": "2026-07",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_upload"
    assert body["upload_url"].startswith("/api/uploads/blob?token=")


def test_register_upload_reports_duplicate_for_an_existing_sha256():
    _override_conn([{"id": "bronze-1"}])

    response = client.post(
        "/api/uploads",
        json={"sha256_hex": "a" * 64, "size_bytes": 1024, "content_type": "application/pdf"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "duplicate",
        "bronze_file_id": "bronze-1",
        "upload_url": None,
        "expires_in_seconds": None,
    }


def test_register_upload_rejects_a_malformed_sha256():
    _override_conn([])

    response = client.post(
        "/api/uploads",
        json={"sha256_hex": "not-a-hash", "size_bytes": 1024, "content_type": "application/pdf"},
    )

    assert response.status_code == 422


def test_register_upload_requires_auth():
    app.dependency_overrides.pop(get_current_user_id, None)

    response = client.post(
        "/api/uploads",
        json={"sha256_hex": "a" * 64, "size_bytes": 1024, "content_type": "application/pdf"},
    )

    assert response.status_code == 401


# --- PUT /api/uploads/blob ---------------------------------------------------


def _upload_token(**overrides):
    fields = {
        "user_id": USER_ID,
        "sha256_hex": hashlib.sha256(b"a,b\n1,2\n").hexdigest(),
        "institution": "scotiabank",
        "period": "2026-07",
        "secret": SECRET,
    }
    fields.update(overrides)
    return create_upload_token(**fields)


def test_upload_blob_stores_a_new_file_and_enqueues_a_job(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(uploads_module, "get_blob_storage", lambda: fake_blob)
    _override_conn(
        [
            {"id": "bronze-1"},  # insert_bronze_file
            {"id": "job-1"},  # enqueue_job
        ]
    )
    token = _upload_token()

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=b"a,b\n1,2\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "queued",
        "bronze_file_id": "bronze-1",
        "upload_url": None,
        "expires_in_seconds": None,
    }
    expected_sha256 = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert fake_blob.calls[0][0] == f"bronze/{USER_ID}/{expected_sha256}"


def test_upload_blob_rejects_a_token_minted_for_another_user():
    _override_conn([])
    token = _upload_token(user_id="someone-else")

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=b"a,b\n1,2\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 403


def test_upload_blob_rejects_an_invalid_token():
    _override_conn([])

    response = client.put(
        "/api/uploads/blob?token=garbage",
        content=b"a,b\n1,2\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 401


def test_upload_blob_rejects_bytes_that_dont_match_the_declared_sha256():
    _override_conn([])
    token = _upload_token(sha256_hex="a" * 64)

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=b"different bytes entirely",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 422
    assert "sha256" in response.json()["detail"]


def test_upload_blob_rejects_content_that_doesnt_match_declared_type():
    _override_conn([])
    binary_body = b"\x00\x01MZ\x02"
    token = _upload_token(sha256_hex=hashlib.sha256(binary_body).hexdigest())

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=binary_body,
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 422


def test_upload_blob_returns_502_when_blob_storage_fails(monkeypatch):
    fake_blob = FakeBlobStorage(error=BlobUploadError("boom"))
    monkeypatch.setattr(uploads_module, "get_blob_storage", lambda: fake_blob)
    _override_conn([])
    token = _upload_token()

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=b"a,b\n1,2\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 502


def test_upload_blob_reports_duplicate_on_insert_conflict(monkeypatch):
    fake_blob = FakeBlobStorage()
    monkeypatch.setattr(uploads_module, "get_blob_storage", lambda: fake_blob)
    _override_conn(
        [
            None,  # insert_bronze_file lost the race (ON CONFLICT DO NOTHING)
            {"id": "bronze-existing"},  # find_by_sha256 fallback
        ]
    )
    token = _upload_token()

    response = client.put(
        f"/api/uploads/blob?token={token}",
        content=b"a,b\n1,2\n",
        headers={"content-type": "text/csv"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    assert response.json()["bronze_file_id"] == "bronze-existing"


# --- GET /api/uploads/{id}/status --------------------------------------------


def test_upload_status_returns_the_jobs_current_state():
    _override_conn(
        [{"bronze_file_id": "bronze-1", "status": "parsing", "error": None}]
    )

    response = client.get("/api/uploads/bronze-1/status")

    assert response.status_code == 200
    assert response.json() == {"bronze_file_id": "bronze-1", "status": "parsing", "error": None}


def test_upload_status_404s_for_an_unknown_bronze_file():
    _override_conn([None])

    response = client.get("/api/uploads/does-not-exist/status")

    assert response.status_code == 404
