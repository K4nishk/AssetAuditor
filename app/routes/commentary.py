"""Audit commentary route (KCH-62 / AA-25).

Read-only: `worker.commentary.generate_audit_commentary` is the only writer
(ADR v1.1.0 §3 — LLM calls only ever originate on the worker, next to
LiteLLM's localhost-only listener; this Vercel-hosted API route can't reach
it directly), so this route only ever serves whatever the worker's last
refresh persisted into `public.audit_commentary`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import audit_commentary as commentary_queries
from app.domain.audit_commentary import decode_observations

router = APIRouter(prefix="/api/commentary", tags=["commentary"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class CommentaryOut(BaseModel):
    as_of: date
    observations: list[str]
    disclosure: str
    model_backend: str


@router.get("", response_model=CommentaryOut)
async def get_commentary(
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> CommentaryOut:
    row = await commentary_queries.get_latest_commentary(conn, user_id=user_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no commentary generated yet")

    return CommentaryOut(
        as_of=row["snapshot_date"],
        observations=decode_observations(row["observations"]),
        disclosure=row["disclosure"],
        model_backend=row["model_backend"],
    )
