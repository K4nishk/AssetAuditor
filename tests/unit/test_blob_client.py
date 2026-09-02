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

from app.uploads.blob import (
    BlobDeleteError,
    BlobUploadError,
    VercelBlobStorage,
    bronze_pathname,
)


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


def test_delete_sends_bearer_auth_and_the_target_url():
    captured = []
    storage = VercelBlobStorage(
        token="secret-token", transport=_fake_transport({"ok": True}, captured)
    )

    storage.delete("https://blob.example/bronze/u1/abc")

    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "https://blob.vercel-storage.com/delete"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert json.loads(request.data) == {"urls": ["https://blob.example/bronze/u1/abc"]}


def test_delete_raises_blob_delete_error_on_transport_failure():
    @contextmanager
    def failing_transport(request, timeout):
        raise OSError("connection refused")
        yield  # pragma: no cover - unreachable, keeps this a generator

    storage = VercelBlobStorage(token="secret-token", transport=failing_transport)

    with pytest.raises(BlobDeleteError):
        storage.delete("https://blob.example/bronze/u1/abc")


def _fake_sequence_transport(response_bodies: list[dict], captured: list):
    """Same shape as `_fake_transport`, but returns one entry of
    `response_bodies` per call in order — needed for `delete_prefix`, which
    makes a `list` GET (possibly several, paginated) followed by a `delete`
    POST, each expecting a different response body."""
    remaining = list(response_bodies)

    @contextmanager
    def transport(request, timeout):
        captured.append(request)
        yield _FakeResponse(json.dumps(remaining.pop(0)).encode("utf-8"))

    return transport


def test_delete_prefix_lists_then_deletes_every_match():
    captured = []
    storage = VercelBlobStorage(
        token="secret-token",
        transport=_fake_sequence_transport(
            [
                {
                    "blobs": [
                        {"url": "https://blob.example/silver/u1/accounts.parquet"},
                        {"url": "https://blob.example/silver/u1/holdings.parquet"},
                    ],
                    "hasMore": False,
                },
                {"ok": True},
            ],
            captured,
        ),
    )

    deleted = storage.delete_prefix("silver/u1/")

    assert deleted == 2
    assert len(captured) == 2
    list_request, delete_request = captured
    assert list_request.full_url == "https://blob.vercel-storage.com/?prefix=silver/u1/"
    assert delete_request.full_url == "https://blob.vercel-storage.com/delete"
    assert json.loads(delete_request.data) == {
        "urls": [
            "https://blob.example/silver/u1/accounts.parquet",
            "https://blob.example/silver/u1/holdings.parquet",
        ]
    }


def test_delete_prefix_paginates_across_a_cursor():
    captured = []
    storage = VercelBlobStorage(
        token="secret-token",
        transport=_fake_sequence_transport(
            [
                {
                    "blobs": [{"url": "https://blob.example/bronze/u1/a"}],
                    "hasMore": True,
                    "cursor": "page-2",
                },
                {
                    "blobs": [{"url": "https://blob.example/bronze/u1/b"}],
                    "hasMore": False,
                },
                {"ok": True},
            ],
            captured,
        ),
    )

    deleted = storage.delete_prefix("bronze/u1/")

    assert deleted == 2
    list_request_1, list_request_2, _delete_request = captured
    assert "cursor" not in list_request_1.full_url
    assert "cursor=page-2" in list_request_2.full_url


def test_delete_prefix_returns_zero_and_skips_delete_when_nothing_matches():
    captured = []
    storage = VercelBlobStorage(
        token="secret-token",
        transport=_fake_sequence_transport([{"blobs": [], "hasMore": False}], captured),
    )

    deleted = storage.delete_prefix("gold/u1/")

    assert deleted == 0
    assert len(captured) == 1  # only the list call, no delete POST


def test_delete_prefix_raises_blob_delete_error_on_list_failure():
    @contextmanager
    def failing_transport(request, timeout):
        raise OSError("connection refused")
        yield  # pragma: no cover - unreachable, keeps this a generator

    storage = VercelBlobStorage(token="secret-token", transport=failing_transport)

    with pytest.raises(BlobDeleteError):
        storage.delete_prefix("bronze/u1/")
