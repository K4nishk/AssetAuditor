"""Hard-purge orchestration for `DELETE /account` (KCH-45 / AA-10).

Ties together the RLS-scoped row deletes (`app.db.queries.account_lifecycle`),
blob deletes (`app.uploads.blob`), and the Supabase Auth Admin identity
delete (`app.auth_admin`) into one ordered routine. mvp.md's AA-10 spec calls
this "the purge job"; there's no async job queue it runs on (`etl_jobs` is
bronze-file-scoped, not a generic job table, and this is a one-shot
user-triggered action, not a recurring sweep like `worker.retention`) — it
runs synchronously inside the `DELETE /account` request, once
`app.domain.account_lifecycle.is_reauth_fresh` has already gated the request.

Split into two functions rather than one so the caller can commit the DB
transaction before attempting the irreversible, non-transactional blob/
identity deletes:

1. `purge_account_rows` — everything inside the RLS-scoped connection's
   transaction: redact this user's `lineage_events` to hash-only tombstones,
   capture bronze blob URLs before their rows disappear, then delete every
   row (see `app.db.queries.account_lifecycle` for the exact cascade/table
   list). Returns those captured URLs alongside the row counts so the second
   step doesn't need to re-query after the rows are gone.
2. `purge_account_external` — once step 1's transaction has committed: best
   -effort blob deletes (captured bronze URLs + `delete_prefix` for the
   silver/gold layers, whose exact URLs no table records — see
   `BlobStorage.delete_prefix`'s docstring) and finally the Supabase Auth
   Admin identity delete. A failure here leaves a fully row-purged account
   with a retryable blob/identity cleanup rather than a rolled-back purge
   that already deleted blobs — the DB purge is the point of no return, not
   this step.

Bronze/silver/gold Blob pathnames are all `{layer}/{user_id}/...`
(`app.uploads.blob.bronze_pathname`, `worker.gold.silver_pathname`/
`gold_pathname`) — the prefixes below are that convention's `{layer}/{user_id}/`
half, duplicated here rather than imported since neither owning module
exposes a bare prefix function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import asyncpg

from app.auth_admin import AuthAdminClient
from app.db.queries.account_lifecycle import (
    PurgeRowCounts,
    purge_user_rows,
    redact_lineage_events,
    unpurged_bronze_blob_urls,
)
from app.uploads.blob import BlobStorage
from worker.lineage import emit_lineage_event, new_run_id

logger = logging.getLogger("app.account_purge")

_ACCOUNT_DELETION_STEP = "account_deletion"


def _blob_prefixes(user_id: str) -> tuple[str, str, str]:
    return (f"bronze/{user_id}/", f"silver/{user_id}/", f"gold/{user_id}/")


@dataclass(frozen=True)
class AccountRowPurgeResult:
    run_id: str
    row_counts: PurgeRowCounts
    lineage_redacted: int
    bronze_blob_urls: list[str]


async def purge_account_rows(
    conn: asyncpg.Connection, *, user_id: str
) -> AccountRowPurgeResult:
    """Redact lineage, capture bronze blob URLs, and delete every row for
    `user_id`. Caller commits the transaction `conn` belongs to (see module
    docstring) before calling `purge_account_external`."""
    run_id = new_run_id()
    await emit_lineage_event(
        conn,
        user_id=user_id,
        run_id=run_id,
        step=_ACCOUNT_DELETION_STEP,
        event_type="START",
    )

    lineage_redacted = await redact_lineage_events(conn, user_id=user_id, keep_run_id=run_id)
    bronze_blob_urls = await unpurged_bronze_blob_urls(conn, user_id=user_id)
    row_counts = await purge_user_rows(conn, user_id=user_id)

    await emit_lineage_event(
        conn,
        user_id=user_id,
        run_id=run_id,
        step=_ACCOUNT_DELETION_STEP,
        event_type="COMPLETE",
        facets={
            "lineage_redacted": lineage_redacted,
            "bronze_files_purged": row_counts.bronze_files,
            "accounts_purged": row_counts.accounts,
        },
    )
    return AccountRowPurgeResult(
        run_id=run_id,
        row_counts=row_counts,
        lineage_redacted=lineage_redacted,
        bronze_blob_urls=bronze_blob_urls,
    )


def purge_account_external(
    *, user_id: str, blob: BlobStorage, auth_admin: AuthAdminClient, bronze_blob_urls: list[str]
) -> None:
    """Blob + auth-identity cleanup, called only after the row purge's
    transaction has committed. Deliberately not wrapped in a try/except that
    swallows failures — CLAUDE.md's provenance-first rule means a failed
    purge step must surface as an error, not report success it didn't
    achieve; the caller (`app.routes.account`) lets this raise as a 500 so a
    retry is visibly needed rather than silently skipped."""
    for url in bronze_blob_urls:
        blob.delete(url)
    for prefix in _blob_prefixes(user_id):
        blob.delete_prefix(prefix)
    auth_admin.delete_user(user_id)
