"""Unit tests for `app.uploads.blob.VercelBlobStorage` (KCH-46 / AA-11).

No real network call — `transport` is swapped for a fake that records the
`urllib.request.Request` it received and returns a canned response, so these
verify our request-building/response-parsing logic without ever touching
Vercel. The real HTTP shape stays unverified (documented in blob.py) until
someone runs this against a live `BLOB_READ_WRITE_TOKEN`.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app.uploads.blob import BlobUploadError, VercelBlobStorage, bronze_pathname


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body


def _fake_transport(response_body: dict, captured: list):
    @contextmanager
    def transport(request, timeout):
        captured.append(request)
        yield _FakeResponse(json.dumps(response_body).encode("utf-8"))

    return transport


def test_put_sends_bearer_auth_and_returns_the_response_url():
    captured = []
    storage = VercelBlobStorage(
        token="secret-token",
        transport=_fake_transport({"url": "https://blob.example/bronze/u1/abc"}, captured),
    )

    url = storage.put("bronze/u1/abc", b"file-bytes", "application/pdf")

    assert url == "https://blob.example/bronze/u1/abc"
    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "https://blob.vercel-storage.com/bronze/u1/abc"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert request.get_header("X-content-type") == "application/pdf"
    assert request.get_header("X-api-version") == "12"
    assert request.data == b"file-bytes"


def test_put_raises_blob_upload_error_when_response_has_no_url():
    storage = VercelBlobStorage(
        token="secret-token", transport=_fake_transport({"unexpected": "shape"}, [])
    )

    with pytest.raises(BlobUploadError):
        storage.put("bronze/u1/abc", b"file-bytes", "application/pdf")


def test_put_raises_blob_upload_error_on_transport_failure():
    @contextmanager
    def failing_transport(request, timeout):
        raise OSError("connection refused")
        yield  # pragma: no cover - unreachable, keeps this a generator

    storage = VercelBlobStorage(token="secret-token", transport=failing_transport)

    with pytest.raises(BlobUploadError):
        storage.put("bronze/u1/abc", b"file-bytes", "application/pdf")


def test_bronze_pathname_is_scoped_by_user_and_content_hash():
    assert bronze_pathname("user-1", "a" * 64) == f"bronze/user-1/{'a' * 64}"
