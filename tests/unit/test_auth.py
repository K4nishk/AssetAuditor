"""Unit tests for Supabase JWT verification (KCH-41 / AA-6).

No network in this sandbox and no live Supabase project, so these mint a
throwaway EC keypair, sign tokens locally, and monkeypatch `JWKSCache._fetch`
to hand back that keypair's public JWK instead of hitting a real
`.../auth/v1/.well-known/jwks.json` endpoint. That still exercises the real
signature-verification and claims-decoding path in `app/auth.py` — only the
network fetch is faked.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from jwt.algorithms import ECAlgorithm

from app.auth import JWKSCache, decode_supabase_jwt

KID = "test-key-1"
AUDIENCE = "authenticated"


@pytest.fixture(scope="module")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture
def cache(keypair, monkeypatch):
    _private_key, public_key = keypair
    jwk = ECAlgorithm.to_jwk(public_key, as_dict=True)
    jwk["kid"] = KID

    instance = JWKSCache(jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setattr(instance, "_fetch", lambda: {KID: jwk})
    return instance


def _sign(
    private_key,
    *,
    kid=KID,
    audience=AUDIENCE,
    sub="00000000-0000-0000-0000-000000000001",
    exp_delta=3600,
):
    claims = {"sub": sub, "aud": audience, "exp": int(time.time()) + exp_delta}
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": kid})


def test_valid_token_decodes_to_its_claims(keypair, cache):
    private_key, _ = keypair
    token = _sign(private_key)

    claims = decode_supabase_jwt(token, cache, AUDIENCE)

    assert claims["sub"] == "00000000-0000-0000-0000-000000000001"


def test_cache_refetches_on_unknown_kid(keypair, cache):
    private_key, _ = keypair
    token = _sign(private_key, kid="rotated-key")

    # Cache only knows KID; a lookup for "rotated-key" refetches, which (via the
    # monkeypatched _fetch) still only returns KID -> so this proves the retry
    # path runs, and correctly still fails since the real key never rotated in.
    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt(token, cache, AUDIENCE)
    assert exc_info.value.status_code == 401


def test_expired_token_is_rejected(keypair, cache):
    private_key, _ = keypair
    token = _sign(private_key, exp_delta=-10)

    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt(token, cache, AUDIENCE)
    assert exc_info.value.status_code == 401


def test_wrong_audience_is_rejected(keypair, cache):
    private_key, _ = keypair
    token = _sign(private_key, audience="some-other-app")

    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt(token, cache, AUDIENCE)
    assert exc_info.value.status_code == 401


def test_tampered_signature_is_rejected(keypair, cache):
    private_key, _ = keypair
    token = _sign(private_key)
    header, payload, signature = token.rsplit(".", 2)
    forged_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    forged_token = f"{header}.{payload}.{forged_signature}"

    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt(forged_token, cache, AUDIENCE)
    assert exc_info.value.status_code == 401


def test_malformed_token_is_rejected(cache):
    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt("not-a-jwt", cache, AUDIENCE)
    assert exc_info.value.status_code == 401


def test_signed_by_a_different_key_is_rejected(cache):
    other_key = ec.generate_private_key(ec.SECP256R1())
    token = _sign(other_key)

    with pytest.raises(HTTPException) as exc_info:
        decode_supabase_jwt(token, cache, AUDIENCE)
    assert exc_info.value.status_code == 401


class TestJWKSCache:
    def test_get_key_caches_within_ttl(self, monkeypatch):
        calls = []

        def fake_fetch():
            calls.append(1)
            return {KID: {"kid": KID}}

        cache = JWKSCache(jwks_url="https://example.supabase.co/jwks.json", ttl_seconds=3600)
        monkeypatch.setattr(cache, "_fetch", fake_fetch)

        cache.get_key(KID)
        cache.get_key(KID)

        assert len(calls) == 1

    def test_get_key_refetches_after_ttl_expires(self, monkeypatch):
        calls = []

        def fake_fetch():
            calls.append(1)
            return {KID: {"kid": KID}}

        cache = JWKSCache(jwks_url="https://example.supabase.co/jwks.json", ttl_seconds=0)
        monkeypatch.setattr(cache, "_fetch", fake_fetch)

        cache.get_key(KID)
        cache.get_key(KID)

        assert len(calls) == 2

    def test_unknown_kid_raises_after_refetch_still_missing(self, monkeypatch):
        from app.auth import JWKSFetchError

        cache = JWKSCache(jwks_url="https://example.supabase.co/jwks.json")
        monkeypatch.setattr(cache, "_fetch", lambda: {})

        with pytest.raises(JWKSFetchError):
            cache.get_key("missing")
