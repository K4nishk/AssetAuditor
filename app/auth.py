"""Supabase JWT verification (JWKS cache) and the `user_id` dependency (KCH-41 / AA-6).

Supabase Auth signs access tokens asymmetrically; the public signing keys are
published at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`. Verifying locally
against a cached copy of that JWKS (rather than round-tripping to Supabase on
every request) is what makes this dependency cheap enough to run on every
route; the cache is what keeps it from hammering that endpoint while still
picking up Supabase's periodic key rotation.

CLAUDE.md hard rule #4: `user_id` comes from the JWT, never the request body.
`get_current_user_id` is the only sanctioned source of `user_id` for routes.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_ALGORITHMS = ["ES256", "RS256"]
_JWKS_TTL_SECONDS = 3600.0
_FETCH_TIMEOUT_SECONDS = 5.0

_bearer_scheme = HTTPBearer(auto_error=False)


class JWKSFetchError(RuntimeError):
    """The JWKS endpoint could not be reached, or had no key for the given `kid`."""


@dataclass
class JWKSCache:
    """Caches a Supabase project's JWKS keyed by `kid`.

    Refetches on TTL expiry or when asked for a `kid` it hasn't seen yet, so a
    key rotated on Supabase's side is picked up on the next verification
    rather than requiring a deploy.
    """

    jwks_url: str
    ttl_seconds: float = _JWKS_TTL_SECONDS
    _fetched_at: float = field(default=0.0, init=False, repr=False)
    _keys_by_kid: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def _fetch(self) -> dict[str, dict[str, Any]]:
        try:
            with urllib.request.urlopen(  # noqa: S310 - fixed https URL, not user input
                self.jwks_url, timeout=_FETCH_TIMEOUT_SECONDS
            ) as response:
                payload = json.loads(response.read())
        except (OSError, ValueError) as exc:
            raise JWKSFetchError(f"failed to fetch JWKS from {self.jwks_url}") from exc
        return {key["kid"]: key for key in payload.get("keys", []) if "kid" in key}

    def get_key(self, kid: str) -> dict[str, Any]:
        is_stale = (time.monotonic() - self._fetched_at) > self.ttl_seconds
        if is_stale or kid not in self._keys_by_kid:
            self._keys_by_kid = self._fetch()
            self._fetched_at = time.monotonic()

        try:
            return self._keys_by_kid[kid]
        except KeyError:
            raise JWKSFetchError(f"no signing key found for kid={kid!r}") from None


_cache: JWKSCache | None = None


def _get_cache() -> JWKSCache:
    global _cache
    if _cache is None:
        supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
        _cache = JWKSCache(f"{supabase_url}/auth/v1/.well-known/jwks.json")
    return _cache


def reset_jwks_cache() -> None:
    """Drop the process-wide JWKS cache singleton. Test-only."""
    global _cache
    _cache = None


def decode_supabase_jwt(token: str, cache: JWKSCache, audience: str) -> dict[str, Any]:
    """Verify `token`'s signature against `cache` and return its claims.

    Raises `HTTPException(401)` for every failure mode (malformed token,
    unknown `kid`, bad signature, expired token, wrong audience) so callers
    never have to distinguish "invalid" from "untrusted".
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed token") from exc

    kid = header.get("kid")
    if not kid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing kid header")

    try:
        jwk = cache.get_key(kid)
    except JWKSFetchError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "unable to verify token signature"
        ) from exc

    signing_key = jwt.PyJWK.from_dict(jwk).key
    try:
        claims: dict[str, Any] = jwt.decode(
            token, signing_key, algorithms=_ALGORITHMS, audience=audience
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token") from exc

    return claims


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency: verify the bearer JWT and return its `sub` claim.

    Routes depend on this instead of ever trusting a `user_id` supplied by
    the client (CLAUDE.md hard rule #4); it is also the identity handed to
    `app.db.pool.rls_connection` so Postgres RLS enforces the same boundary.
    """
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")

    audience = os.environ.get("SUPABASE_JWT_AUD", "authenticated")
    claims = decode_supabase_jwt(credentials.credentials, _get_cache(), audience)

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub claim")
    return str(sub)
