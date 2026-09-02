"""Parse-confirm staged-row read/edit/confirm routes (KCH-52 / AA-17).

Flow: a job that finished extraction lands in `etl_jobs.status = 'needs_user'`
with its parsed rows in `staged_rows` (`worker.adapters.base.StagedRowDraft`
shape). The frontend's parse-confirm screen lists them
(`GET /api/staged/{job_id}/rows`), highlights low-confidence ones
(`app.domain.staged_rows.is_low_confidence`), lets the user fix any of them
inline (`PATCH .../rows/{row_id}`, logged as `method='manual_correction'` per
docs/vault/30-architecture/ETL-Pipeline.md), then confirms the whole batch
(`POST .../confirm`), which resolves every row's natural-key references
(`app.db.queries.silver`) into the silver tables and marks the job `done`.

Extraction dispatch itself (worker calling adapters/pdfplumber/LLM tiers and
writing `staged_rows`) is not wired into `worker/main.py`'s job loop yet —
that remains a gap outside this issue's route-table contract
(`templates/backend/v1_fastapi_modular/README.md`); tests exercise this
module by seeding `staged_rows` directly, same as production would once that
wiring lands.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import etl_jobs, staged_rows
from app.db.queries.silver import SilverResolutionError, write_confirmed_rows
from app.domain.staged_rows import decode_payload, is_low_confidence
from worker.lineage import LineageEmitter, new_run_id

router = APIRouter(prefix="/api/staged", tags=["staged"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class StagedRowOut(BaseModel):
    id: str
    entity: str
    payload: dict[str, Any]
    confidence: float | None
    method: str
    is_low_confidence: bool
    confirmed_at: str | None
    created_at: str


class StagedRowsResponse(BaseModel):
    job_id: str
    job_status: str
    rows: list[StagedRowOut]


class EditRowRequest(BaseModel):
    payload: dict[str, Any]


class ConfirmResponse(BaseModel):
    job_id: str
    status: str
    confirmed_row_count: int
    silver_write_summary: dict[str, int]


def _row_out(record: asyncpg.Record) -> StagedRowOut:
    confirmed_at = record["confirmed_at"]
    return StagedRowOut(
        id=str(record["id"]),
        entity=record["entity"],
        payload=decode_payload(record["payload"]),
        confidence=record["confidence"],
        method=record["method"],
        is_low_confidence=is_low_confidence(record["confidence"]),
        confirmed_at=confirmed_at.isoformat() if confirmed_at else None,
        created_at=record["created_at"].isoformat(),
    )


async def _get_job_or_404(
    conn: asyncpg.Connection, *, user_id: str, job_id: str
) -> asyncpg.Record:
    job = await etl_jobs.get_job(conn, user_id=user_id, job_id=job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    return job


def _require_needs_user(job: asyncpg.Record) -> None:
    if job["status"] != "needs_user":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"job is {job['status']!r}, not awaiting confirmation",
        )


@router.get("/{job_id}/rows", response_model=StagedRowsResponse)
async def list_staged_rows(
    job_id: UUID,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> StagedRowsResponse:
    job = await _get_job_or_404(conn, user_id=user_id, job_id=str(job_id))
    rows = await staged_rows.list_rows_for_job(conn, user_id=user_id, job_id=str(job_id))
    return StagedRowsResponse(
        job_id=str(job_id),
        job_status=job["status"],
        rows=[_row_out(row) for row in rows],
    )


@router.patch("/{job_id}/rows/{row_id}", response_model=StagedRowOut)
async def edit_staged_row(
    job_id: UUID,
    row_id: UUID,
    body: EditRowRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> StagedRowOut:
    job = await _get_job_or_404(conn, user_id=user_id, job_id=str(job_id))
    _require_needs_user(job)
    if not body.payload:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "payload must not be empty")

    updated = await staged_rows.update_row_payload(
        conn, user_id=user_id, job_id=str(job_id), row_id=str(row_id), payload=body.payload
    )
    if updated is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "no such staged row, or it is already confirmed/deactivated",
        )
    return _row_out(updated)


@router.post("/{job_id}/confirm", response_model=ConfirmResponse)
async def confirm_staged_rows(
    job_id: UUID,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ConfirmResponse:
    job_id_str = str(job_id)
    job = await _get_job_or_404(conn, user_id=user_id, job_id=job_id_str)
    _require_needs_user(job)

    rows = await staged_rows.list_rows_for_job(
        conn, user_id=user_id, job_id=job_id_str, unconfirmed_only=True
    )

    run_id = await staged_rows.find_run_id_for_job(
        conn, user_id=user_id, job_id=job_id_str
    ) or new_run_id()
    emitter = LineageEmitter(conn, user_id=user_id, job_id=job_id_str, run_id=run_id)
    await emitter.start("confirm", facets={"row_count": len(rows)})

    try:
        summary = await write_confirmed_rows(conn, user_id=user_id, rows=rows)
    except SilverResolutionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    row_ids = [str(row["id"]) for row in rows]
    await staged_rows.mark_confirmed(conn, user_id=user_id, job_id=job_id_str, row_ids=row_ids)

    marked_job = await etl_jobs.mark_job_done(conn, user_id=user_id, job_id=job_id_str)
    if marked_job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "job status changed concurrently")

    await emitter.complete("confirm", facets={"silver_write_summary": summary})

    return ConfirmResponse(
        job_id=job_id_str,
        status=marked_job["status"],
        confirmed_row_count=len(row_ids),
        silver_write_summary=summary,
    )
