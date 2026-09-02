"""`DELETE /account` — hard purge, requires re-auth (KCH-45 / AA-10).

templates/backend/v1_fastapi_modular/README.md's route table: `DELETE
/account | user + re-auth | schedule hard purge (rows, blobs, auth identity
<=30d)`. Soft-freeze (`POST /profile/deactivate`/`reactivate`) lives in
`app.routes.profile` instead — this route is the heavier, irreversible
sibling.

Re-auth freshness (`app.domain.account_lifecycle.is_reauth_fresh`) uses the
bearer token's own `iat` claim rather than a second Supabase credential
round trip — see that module's docstring. The purge itself is
`app.account_purge`'s two-step routine: row purge inside one RLS-scoped
transaction, then blob + Supabase Auth Admin identity deletes only after
that transaction has committed (irreversible steps last).
"""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.account_purge import purge_account_external, purge_account_rows
from app.auth import get_current_claims
from app.auth_admin import get_auth_admin_client
from app.db.pool import rls_connection
from app.domain.account_lifecycle import is_reauth_fresh
from app.uploads.blob import get_blob_storage

router = APIRouter(prefix="/api/account", tags=["account"])


class AccountDeleteOut(BaseModel):
    status: str
    run_id: str
    accounts_purged: int
    bronze_files_purged: int
    lineage_redacted: int


def _require_fresh_reauth(claims: dict) -> str:
    """Return the validated `sub`, or raise 401/403.

    401 for a malformed token (missing `sub`/`iat` — `get_current_claims`
    already verified the signature, so this would mean an unexpectedly
    shaped but validly-signed token); 403 specifically for "signed in, but
    not recently enough" so the frontend can tell the two apart and prompt a
    fresh sign-in rather than a full re-login.
    """
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing sub claim")

    issued_at = claims.get("iat")
    if issued_at is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token missing iat claim")

    if not is_reauth_fresh(float(issued_at), now_epoch_seconds=time.time()):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "re-authentication required: sign in again immediately before deleting your account",
        )
    return str(sub)


@router.delete("", response_model=AccountDeleteOut)
async def delete_account(
    claims: dict = Depends(get_current_claims),
) -> AccountDeleteOut:
    user_id = _require_fresh_reauth(claims)

    async with rls_connection(user_id) as conn:
        row_result = await purge_account_rows(conn, user_id=user_id)
    # `rls_connection`'s transaction has committed by this point (the `async
    # with` block exited normally) — only now do we attempt the
    # irreversible, non-transactional blob/identity deletes.

    blob = get_blob_storage()
    auth_admin = get_auth_admin_client()
    await asyncio.to_thread(
        purge_account_external,
        user_id=user_id,
        blob=blob,
        auth_admin=auth_admin,
        bronze_blob_urls=row_result.bronze_blob_urls,
    )

    return AccountDeleteOut(
        status="purged",
        run_id=row_result.run_id,
        accounts_purged=row_result.row_counts.accounts,
        bronze_files_purged=row_result.row_counts.bronze_files,
        lineage_redacted=row_result.lineage_redacted,
    )
