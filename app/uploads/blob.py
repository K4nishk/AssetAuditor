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
import urllib.parse
import urllib.request
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

_BLOB_API_BASE = "https://blob.vercel-storage.com"


class BlobUploadError(Exception):
    """Vercel Blob rejected or could not be reached for an upload."""


class BlobDeleteError(Exception):
    """Vercel Blob rejected or could not be reached for a delete (AA-19)."""


class BlobStorage(Protocol):
    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        """Upload `data` to `pathname` and return the resulting public URL."""
        ...

    def delete(self, url: str) -> None:
        """Delete the blob at `url` (AA-19's retention sweeper)."""
        ...

    def delete_prefix(self, prefix: str) -> int:
        """Delete every blob whose pathname starts with `prefix`; returns the
        count deleted (AA-10's account purge — bronze/silver/gold all key
        their pathnames off `{layer}/{user_id}/...`, so one prefix per layer
        clears everything for a deleted account without a DB row to look up
        the exact pathname from, unlike `delete`)."""
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

    def delete(self, url: str) -> None:
        """`bronze_files.blob_url` is the authoritative URL for a row (not a
        re-derived pathname) — this is what AA-19's retention sweeper calls
        with it once a bronze file passes its 14-day TTL."""
        request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
            f"{_BLOB_API_BASE}/delete",
            data=json.dumps({"urls": [url]}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                # Same unverified caveat as `put` — no network/token in this
                # sandbox to confirm this request shape against a live store.
                "x-api-version": "12",
            },
        )
        opener = self.transport or urllib.request.urlopen
        try:
            with opener(request, timeout=30.0) as response:
                response.read()
        except OSError as exc:
            raise BlobDeleteError(f"failed to delete {url!r} from Vercel Blob") from exc

    def delete_prefix(self, prefix: str) -> int:
        """AA-10's account purge: silver/gold pathnames are deterministic
        (`{layer}/{user_id}/...`, `worker.gold.silver_pathname`/`gold_pathname`)
        but no DB table records the URLs Vercel Blob handed back for them, so
        this lists the prefix first rather than trying to re-derive URLs."""
        urls = self._list_urls(prefix)
        if not urls:
            return 0

        request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
            f"{_BLOB_API_BASE}/delete",
            data=json.dumps({"urls": urls}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "content-type": "application/json",
                # Same unverified caveat as `put`/`delete`.
                "x-api-version": "12",
            },
        )
        opener = self.transport or urllib.request.urlopen
        try:
            with opener(request, timeout=30.0) as response:
                response.read()
        except OSError as exc:
            raise BlobDeleteError(f"failed to delete prefix {prefix!r} from Vercel Blob") from exc
        return len(urls)

    def _list_urls(self, prefix: str) -> list[str]:
        """Page through Vercel Blob's list endpoint collecting every URL under `prefix`."""
        urls: list[str] = []
        cursor: str | None = None
        opener = self.transport or urllib.request.urlopen
        while True:
            query = f"prefix={urllib.parse.quote(prefix)}"
            if cursor:
                query += f"&cursor={urllib.parse.quote(cursor)}"
            request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
                f"{_BLOB_API_BASE}/?{query}",
                method="GET",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "x-api-version": "12",
                },
            )
            try:
                with opener(request, timeout=30.0) as response:
                    body = json.loads(response.read())
            except (OSError, ValueError) as exc:
                raise BlobDeleteError(
                    f"failed to list prefix {prefix!r} from Vercel Blob"
                ) from exc

            urls.extend(blob["url"] for blob in body.get("blobs", []))
            cursor = body.get("cursor")
            if not body.get("hasMore") or not cursor:
                break
        return urls


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
