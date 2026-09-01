"""Unit tests for `app.uploads.signing` (KCH-46 / AA-11).

The signed upload token is the only thing carrying (user_id, sha256,
institution, period) between `POST /uploads` and `PUT /uploads/blob` — no DB
row exists for a file until its bytes land in Blob storage, so a forged or
stale token must never verify.
"""

from __future__ import annotations

import pytest

from app.uploads.signing import (
    InvalidUploadToken,
    create_upload_token,
    verify_upload_token,
)

SECRET = b"test-upload-secret"


def _token(**overrides):
    fields = {
        "user_id": "user-1",
        "sha256_hex": "a" * 64,
        "institution": "scotiabank",
        "period": "2026-07",
        "secret": SECRET,
    }
    fields.update(overrides)
    return create_upload_token(**fields)


def test_round_trips_the_payload():
    token = _token()

    payload = verify_upload_token(token, secret=SECRET)

    assert payload.user_id == "user-1"
    assert payload.sha256_hex == "a" * 64
    assert payload.institution == "scotiabank"
    assert payload.period == "2026-07"


def test_rejects_a_token_signed_with_a_different_secret():
    token = _token()

    with pytest.raises(InvalidUploadToken, match="signature"):
        verify_upload_token(token, secret=b"wrong-secret")


def test_rejects_a_tampered_payload():
    token = _token()
    payload_b64, signature = token.split(".", 1)
    tampered = payload_b64 + "x." + signature

    with pytest.raises(InvalidUploadToken):
        verify_upload_token(tampered, secret=SECRET)


def test_rejects_a_malformed_token():
    with pytest.raises(InvalidUploadToken, match="malformed"):
        verify_upload_token("not-a-real-token", secret=SECRET)


def test_rejects_an_expired_token():
    token = _token(now=1_000_000.0, ttl_seconds=60)

    with pytest.raises(InvalidUploadToken, match="expired"):
        verify_upload_token(token, secret=SECRET, now=1_000_061.0)


def test_accepts_a_token_still_within_its_ttl():
    token = _token(now=1_000_000.0, ttl_seconds=60)

    payload = verify_upload_token(token, secret=SECRET, now=1_000_059.0)

    assert payload.sha256_hex == "a" * 64


def test_optional_institution_and_period_round_trip_as_none():
    token = _token(institution=None, period=None)

    payload = verify_upload_token(token, secret=SECRET)

    assert payload.institution is None
    assert payload.period is None
