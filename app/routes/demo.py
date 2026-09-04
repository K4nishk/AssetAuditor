"""Demo mode: seed-from-fixtures button for the public blog demo (KCH-69 / AA-32).

`POST /api/demo/seed` re-derives the *entire* demo account from
`data/samples/` through the same pipeline a real upload uses (bronze row ->
`etl_jobs` -> `staged_rows` -> confirm -> silver -> `worker.gold.rebuild_gold`),
so the demo also exercises AA-23's lineage drill-down honestly instead of
faking gold numbers directly. "Never touches real data" is enforced by
`_require_demo_user`: the route only ever writes to the one account whose id
matches the `DEMO_USER_ID` env var, no matter which authenticated caller
hits it — a real user's JWT simply gets a 403, whatever bytes are in
`data/samples/`.

Every fixture-derived write goes through the confirmed `write_confirmed_rows`
resolver (never a raw insert), on the same RLS-scoped connection every other
route uses, inside one transaction (`app.db.pool.rls_connection`) — so a
seed that fails partway rolls back to whatever the demo account held before
the click, rather than leaving a half-loaded state visible to the next
visitor.

Re-clicking the button must not accumulate duplicate silver rows (silver is
append-only, `app.db.queries.silver`'s own docstring), so every seed starts
by purging whatever the demo account currently holds
(`app.db.queries.account_lifecycle.purge_user_rows`, the same routine AA-10's
account deletion uses) and clearing its blob prefixes, before reloading.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import bronze_files, etl_jobs, staged_rows
from app.db.queries.account_lifecycle import purge_user_rows
from app.db.queries.silver import SilverResolutionError, write_confirmed_rows
from app.db.queries.users_profile import upsert_profile
from app.domain.demo import (
    ALEX_MOCK_PROFILE,
    DEMO_FIXTURES,
    DEMO_SNAPSHOT_DATE,
    FIXTURES_SKIPPED,
    DemoFixture,
)
from app.uploads.blob import BlobStorage, BlobUploadError, bronze_pathname, get_blob_storage
from worker.gold import rebuild_gold
from worker.lineage import LineageEmitter, new_run_id

router = APIRouter(prefix="/api/demo", tags=["demo"])

_SAMPLES_DIR = Path(__file__).resolve().parents[2] / "data" / "samples"
_SILVER_ENTITIES = ("account", "holding", "lot", "transaction", "liability")


def _demo_user_id() -> str | None:
    return os.environ.get("DEMO_USER_ID")


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


def _require_demo_user(user_id: str) -> None:
    demo_user_id = _demo_user_id()
    if not demo_user_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "demo mode is not configured")
    if user_id != demo_user_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "seed-from-fixtures only runs against the demo account",
        )


class DemoStatusOut(BaseModel):
    configured: bool
    is_demo_user: bool


class DemoSeedOut(BaseModel):
    fixtures_loaded: list[str]
    fixtures_skipped: list[str]
    silver_write_summary: dict[str, int]
    room_events_written: int
    total_assets_cad: str
    total_liabilities_cad: str
    net_worth_cad: str


@router.get("/status", response_model=DemoStatusOut)
async def demo_status(user_id: str = Depends(get_current_user_id)) -> DemoStatusOut:
    demo_user_id = _demo_user_id()
    return DemoStatusOut(
        configured=demo_user_id is not None,
        is_demo_user=demo_user_id is not None and user_id == demo_user_id,
    )


async def _insert_bronze_file(
    conn: asyncpg.Connection, *, user_id: str, sha256_hex: str, institution: str, blob_url: str
) -> str:
    """Same lost-the-race fallback `app.routes.manual_entry` uses — after
    `purge_user_rows` this shouldn't happen for the demo account, but two
    overlapping seed clicks racing each other is exactly the scenario a
    public, unauthenticated-feeling demo button invites."""
    inserted = await bronze_files.insert_bronze_file(
        conn, user_id=user_id, sha256_hex=sha256_hex, institution=institution,
        period=None, blob_url=blob_url,
    )
    if inserted is not None:
        return str(inserted["id"])
    existing = await bronze_files.find_by_sha256(conn, user_id=user_id, sha256_hex=sha256_hex)
    if existing is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "bronze file vanished after insert conflict"
        )
    return str(existing["id"])


async def _seed_one_fixture(
    conn: asyncpg.Connection, *, user_id: str, blob: BlobStorage, fixture: DemoFixture
) -> dict[str, int]:
    raw = (_SAMPLES_DIR / fixture.filename).read_bytes()
    if not fixture.adapter.detect(raw):
        # data/samples/ is the adapter contract (CLAUDE.md read-first map) —
        # a fixture that no longer matches its own adapter's detect() means
        # the contract has silently drifted, which must fail loudly rather
        # than seed whatever detect() happens to fall back to.
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"fixture {fixture.filename!r} no longer matches worker.adapters."
            f"{fixture.adapter.__name__.rsplit('.', 1)[-1]}.detect()",
        )
    drafts = fixture.adapter.parse(raw)

    sha256_hex = hashlib.sha256(raw).hexdigest()
    try:
        blob_url = blob.put(bronze_pathname(user_id, sha256_hex), raw, fixture.content_type)
    except BlobUploadError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"failed to store fixture {fixture.filename!r}"
        ) from exc

    bronze_file_id = await _insert_bronze_file(
        conn,
        user_id=user_id,
        sha256_hex=sha256_hex,
        institution=fixture.adapter.INSTITUTION,
        blob_url=blob_url,
    )
    job = await etl_jobs.insert_needs_user_job(conn, user_id=user_id, bronze_file_id=bronze_file_id)
    job_id = str(job["id"])

    emitter = LineageEmitter(conn, user_id=user_id, job_id=job_id, run_id=new_run_id())
    await emitter.start("stage", facets={"source": "demo_seed", "row_count": len(drafts)})
    staged = [
        await staged_rows.insert_draft(
            conn,
            user_id=user_id,
            job_id=job_id,
            entity=draft.entity,
            payload=draft.payload,
            confidence=draft.confidence,
            method=draft.method,
        )
        for draft in drafts
    ]
    await emitter.complete("stage", facets={"row_count": len(staged)})

    await emitter.start("confirm", facets={"row_count": len(staged)})
    try:
        summary = await write_confirmed_rows(conn, user_id=user_id, rows=staged)
    except SilverResolutionError as exc:
        await emitter.fail("confirm", error=str(exc))
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc

    row_ids = [str(row["id"]) for row in staged]
    await staged_rows.mark_confirmed(conn, user_id=user_id, job_id=job_id, row_ids=row_ids)
    marked = await etl_jobs.mark_job_done(conn, user_id=user_id, job_id=job_id)
    if marked is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "demo job status changed concurrently")
    await emitter.complete("confirm", facets={"silver_write_summary": summary})

    return summary


@router.post("/seed", response_model=DemoSeedOut)
async def seed_demo_data(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> DemoSeedOut:
    _require_demo_user(user_id)
    blob = get_blob_storage()

    await purge_user_rows(conn, user_id=user_id)
    for prefix in (f"bronze/{user_id}/", f"silver/{user_id}/", f"gold/{user_id}/"):
        blob.delete_prefix(prefix)

    silver_summary = dict.fromkeys(_SILVER_ENTITIES, 0)
    loaded: list[str] = []
    for fixture in DEMO_FIXTURES:
        summary = await _seed_one_fixture(conn, user_id=user_id, blob=blob, fixture=fixture)
        for entity, count in summary.items():
            silver_summary[entity] += count
        loaded.append(fixture.filename)

    await upsert_profile(conn, user_id=user_id, **ALEX_MOCK_PROFILE)

    result = await rebuild_gold(
        conn,
        user_id=user_id,
        snapshot_date=DEMO_SNAPSHOT_DATE,
        lineage=LineageEmitter(conn, user_id=user_id),
        blob=blob,
    )

    return DemoSeedOut(
        fixtures_loaded=loaded,
        fixtures_skipped=list(FIXTURES_SKIPPED),
        silver_write_summary=silver_summary,
        room_events_written=result.room_events_written,
        total_assets_cad=str(result.totals.total_assets_cad),
        total_liabilities_cad=str(result.totals.total_liabilities_cad),
        net_worth_cad=str(result.totals.net_worth_cad),
    )
