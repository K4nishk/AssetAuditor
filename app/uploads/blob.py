"""Vercel Blob client for bronze uploads (AA-11).

Bytes are validated (sha256, size, magic bytes — `app/uploads/validation.py`)
inside our own API function before ever reaching Blob storage, so this client
does a plain authenticated server-side `PUT` to Vercel Blob's REST endpoint
rather than minting one of `@vercel/blob`'s browser client-upload tokens —
that flow hands bytes straight from the browser to Blob and would skip our
magic-byte check entirely, which the CLAUDE.md provenance rule and this
issue's spec both require to happen before a bronze row exists.

**Unverified**: no network or `BLOB_READ_WRITE_TOKEN` in this sandbox, so the
request shape below (path, headers, response JSON) has never been exercised
against a real Vercel Blob store. `Protocol`-typed so a fake can stand in for
it in tests and so the real shape can be corrected in one place once someone
runs this against a live token.
"""

from __future__ import annotations

import json
import os
import urllib.request
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

_BLOB_API_BASE = "https://blob.vercel-storage.com"


class BlobUploadError(Exception):
    """Vercel Blob rejected or could not be reached for an upload."""


class BlobStorage(Protocol):
    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        """Upload `data` to `pathname` and return the resulting public URL."""
        ...


@dataclass
class VercelBlobStorage:
    """Real `BlobStorage` backed by Vercel Blob's HTTP API.

    `transport` defaults to `urllib.request.urlopen` (same stdlib-only
    approach as `app.auth.JWKSCache`, keeping this dependency-free) and is
    swappable in tests so no test suite needs real network access.
    """

    token: str
    transport: _Transport | None = None

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
            f"{_BLOB_API_BASE}/{pathname}",
            data=data,
            method="PUT",
            headers={
                "Authorization": f"Bearer {self.token}",
                "x-content-type": content_type,
                # Bronze pathnames are already unique per (user, sha256); a
                # deterministic URL is what makes dedupe-by-sha256 meaningful.
                "x-add-random-suffix": "0",
                # Required by Vercel Blob's REST API — same "unverified"
                # caveat as the rest of this request shape (see module
                # docstring); correct this value in one place if it's stale
                # once someone runs this against a live token.
                "x-api-version": "12",
            },
        )
        opener = self.transport or urllib.request.urlopen
        try:
            with opener(request, timeout=30.0) as response:
                body = json.loads(response.read())
        except (OSError, ValueError) as exc:
            raise BlobUploadError(f"failed to upload {pathname!r} to Vercel Blob") from exc

        try:
            return str(body["url"])
        except KeyError as exc:
            raise BlobUploadError(f"Vercel Blob response missing 'url': {body!r}") from exc


class _BlobResponse(Protocol):
    def read(self) -> bytes: ...


class _Transport(Protocol):
    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> AbstractContextManager[_BlobResponse]: ...


def get_blob_storage() -> VercelBlobStorage:
    return VercelBlobStorage(token=os.environ["BLOB_READ_WRITE_TOKEN"])


def bronze_pathname(user_id: str, sha256_hex: str) -> str:
    return f"bronze/{user_id}/{sha256_hex}"
