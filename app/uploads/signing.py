"""Signed upload tokens — the "signed Blob URL" `POST /uploads` hands back (AA-11).

`bronze_files.blob_url` is `not null` (migration 0001), so a row can't exist
until the bytes have actually landed in Blob storage. That means the state
gathered at `POST /uploads` (sha256, institution, period, which user) has to
travel to the follow-up `PUT /uploads/blob` some other way than a DB row —
this module carries it in a signed, expiring, tamper-evident token embedded
in the upload URL instead, the same HMAC approach CLAUDE.md's `/internal/*`
routes already use for `INTERNAL_HMAC_SECRET`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

DEFAULT_TTL_SECONDS = 900  # 15 minutes — long enough for a slow upload, no longer.


class InvalidUploadToken(Exception):
    """Token is malformed, has a bad signature, or has expired."""


@dataclass(frozen=True)
class UploadTokenPayload:
    user_id: str
    sha256_hex: str
    institution: str | None
    period: str | None
    expires_at: float


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(payload_b64: str, secret: bytes) -> str:
    return hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def create_upload_token(
    *,
    user_id: str,
    sha256_hex: str,
    institution: str | None,
    period: str | None,
    secret: bytes,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """Mint a token scoped to one user + one file, expiring `ttl_seconds` out."""
    expires_at = (now if now is not None else time.time()) + ttl_seconds
    payload = {
        "user_id": user_id,
        "sha256_hex": sha256_hex,
        "institution": institution,
        "period": period,
        "expires_at": expires_at,
    }
    payload_b64 = _b64encode(json.dumps(payload, sort_keys=True).encode("utf-8"))
    signature = _sign(payload_b64, secret)
    return f"{payload_b64}.{signature}"


def verify_upload_token(
    token: str, *, secret: bytes, now: float | None = None
) -> UploadTokenPayload:
    """Verify `token`'s signature and expiry, returning its scoped payload.

    Raises `InvalidUploadToken` for every failure mode (malformed, bad
    signature, expired) so callers never have to distinguish "invalid" from
    "untrusted", matching `app.auth.decode_supabase_jwt`'s convention.
    """
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        raise InvalidUploadToken("malformed token") from None

    expected_signature = _sign(payload_b64, secret)
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidUploadToken("bad signature")

    try:
        raw = json.loads(_b64decode(payload_b64))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidUploadToken("malformed payload") from exc

    try:
        payload = UploadTokenPayload(
            user_id=raw["user_id"],
            sha256_hex=raw["sha256_hex"],
            institution=raw["institution"],
            period=raw["period"],
            expires_at=raw["expires_at"],
        )
    except KeyError as exc:
        raise InvalidUploadToken("payload missing required field") from exc

    current_time = now if now is not None else time.time()
    if current_time > payload.expires_at:
        raise InvalidUploadToken("token expired")

    return payload
