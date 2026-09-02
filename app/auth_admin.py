"""Supabase Auth Admin client — hard-deletes the auth identity (KCH-45 / AA-10).

The last step of `DELETE /account`'s purge: everything else (rows, blobs) can
go through the user's own RLS-scoped session, but removing the `auth.users`
row itself requires Supabase's Admin API (`service_role`, never the anon/user
JWT) — same reasoning `app.uploads.blob` gives for going straight to Vercel
Blob's REST API rather than a browser-facing client-upload token.

**Unverified**: no network or `SUPABASE_SERVICE_ROLE_KEY` in this sandbox, so
the request shape below (`DELETE {SUPABASE_URL}/auth/v1/admin/users/{id}`,
`Authorization`/`apikey` headers) has never been exercised against a real
Supabase project. `Protocol`-typed, same pattern as `app.uploads.blob.BlobStorage`,
so a fake stands in for it in tests and the real shape can be corrected in one
place once someone runs this against a live project.
"""

from __future__ import annotations

import os
import urllib.request
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

_AUTH_ADMIN_TIMEOUT_SECONDS = 30.0


class AuthAdminError(Exception):
    """Supabase's Auth Admin API rejected or could not be reached for a delete."""


class AuthAdminClient(Protocol):
    def delete_user(self, user_id: str) -> None:
        """Hard-delete the Supabase Auth identity for `user_id`."""
        ...


@dataclass
class SupabaseAuthAdminClient:
    supabase_url: str
    service_role_key: str
    transport: _Transport | None = None

    def delete_user(self, user_id: str) -> None:
        request = urllib.request.Request(  # noqa: S310 - fixed https host, not user input
            f"{self.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
            method="DELETE",
            headers={
                "Authorization": f"Bearer {self.service_role_key}",
                "apikey": self.service_role_key,
            },
        )
        opener = self.transport or urllib.request.urlopen
        try:
            with opener(request, timeout=_AUTH_ADMIN_TIMEOUT_SECONDS) as response:
                response.read()
        except OSError as exc:
            raise AuthAdminError(
                f"failed to delete Supabase Auth identity for user {user_id!r}"
            ) from exc


class _AuthAdminResponse(Protocol):
    def read(self) -> bytes: ...


class _Transport(Protocol):
    def __call__(
        self, request: urllib.request.Request, timeout: float
    ) -> AbstractContextManager[_AuthAdminResponse]: ...


def get_auth_admin_client() -> SupabaseAuthAdminClient:
    return SupabaseAuthAdminClient(
        supabase_url=os.environ["SUPABASE_URL"],
        service_role_key=os.environ["SUPABASE_SERVICE_ROLE_KEY"],
    )
