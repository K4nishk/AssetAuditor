"""FastAPI-level wiring for `get_current_user_id` (KCH-41 / AA-6).

Exercises the dependency the way a real route will use it: mounted behind
`Depends(get_current_user_id)` on a throwaway app, hit with a TestClient.
Signature verification itself is covered by tests/unit/test_auth.py; this
file only checks the HTTP-layer contract (401 shape, dependency override
pattern later routes rely on).
"""

from __future__ import annotations

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm

from app.auth import JWKSCache, get_current_user_id

KID = "test-key"
AUDIENCE = "authenticated"


def _make_app(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwk = ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = KID

    cache = JWKSCache(jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setattr(cache, "_fetch", lambda: {KID: jwk})
    monkeypatch.setattr("app.auth._get_cache", lambda: cache)
    monkeypatch.setenv("SUPABASE_JWT_AUD", AUDIENCE)

    app = FastAPI()

    @app.get("/whoami")
    def whoami(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"user_id": user_id}

    return app, private_key


def _sign(private_key, sub="00000000-0000-0000-0000-000000000042"):
    return jwt.encode(
        {"sub": sub, "aud": AUDIENCE, "exp": 9999999999},
        private_key,
        algorithm="ES256",
        headers={"kid": KID},
    )


def test_valid_bearer_token_resolves_user_id(monkeypatch):
    app, private_key = _make_app(monkeypatch)
    client = TestClient(app)
    token = _sign(private_key)

    response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "00000000-0000-0000-0000-000000000042"}


def test_missing_authorization_header_is_401(monkeypatch):
    app, _ = _make_app(monkeypatch)
    client = TestClient(app)

    response = client.get("/whoami")

    assert response.status_code == 401


def test_invalid_token_is_401(monkeypatch):
    app, _ = _make_app(monkeypatch)
    client = TestClient(app)

    response = client.get("/whoami", headers={"Authorization": "Bearer garbage"})

    assert response.status_code == 401
