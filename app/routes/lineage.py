"""Drill-down panel: click a dashboard slice -> its underlying rows (KCH-60 / AA-23).

`GET /api/lineage/slice` is the one route this issue adds. It walks the
chain the schema was built for (migration 0001's `term_buckets`/
`diversification_cuts`/`networth_snapshots` comment: "run_id is not null so
drill-down ... never silently dead-ends"; `worker/lineage.py`'s module
docstring names this route by name):

1. Given a slice selector (which pie, which wedge) plus the snapshot date
   AA-22's dashboard is already showing, look up that gold row's `run_id`.
2. `run_id` identifies one `etl_jobs` processing attempt
   (`worker.lineage.LineageEmitter`) — find the `job_id` its lineage events
   were emitted under, if any.
3. From `job_id`: the source `bronze_files` row (or, once AA-19's retention
   sweeper has purged it, a tombstone — `app.domain.lineage_slice
   .describe_source_file`), and every `staged_rows` row confirmed under that
   job, each carrying its own `method` and `confirmed_at`.

A gold row whose run predates the confirm-route wiring that binds `job_id`
to its lineage events (`app/routes/staged.py`'s `confirm_staged_rows`), or
whose run was never confirmed through that route at all (a gold rebuild
triggered some other way — `worker.gold.rebuild_gold`'s "not wired into a
caller" gap), returns `job_id`/`source_file` as `null` and an empty `rows`
list rather than 404ing: the run itself is real and returned, there is just
nothing further to walk. Only an unknown slice (no gold row at all) is a 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import etl_jobs as etl_jobs_queries
from app.db.queries import lineage_slice as lineage_slice_queries
from app.db.queries import staged_rows as staged_rows_queries
from app.domain.lineage_slice import describe_source_file
from app.domain.staged_rows import decode_payload

router = APIRouter(prefix="/api/lineage", tags=["lineage"])

SliceKind = Literal["term_bucket", "net_worth", "diversification"]


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class SourceFileOut(BaseModel):
    bronze_file_id: str
    institution: str | None
    period: str | None
    is_purged: bool
    blob_url: str | None
    purged_at: str | None


class UnderlyingRowOut(BaseModel):
    id: str
    entity: str
    payload: dict[str, Any]
    method: str
    confirmed_at: str | None


class LineageSliceOut(BaseModel):
    kind: SliceKind
    run_id: str
    job_id: str | None
    source_file: SourceFileOut | None
    rows: list[UnderlyingRowOut]


def _source_file_out(bronze_file: asyncpg.Record) -> SourceFileOut:
    file_status = describe_source_file(
        purged_at=bronze_file["purged_at"], blob_url=bronze_file["blob_url"]
    )
    purged_at = bronze_file["purged_at"]
    return SourceFileOut(
        bronze_file_id=str(bronze_file["id"]),
        institution=bronze_file["institution"],
        period=bronze_file["period"],
        is_purged=file_status.is_purged,
        blob_url=None if file_status.is_purged else bronze_file["blob_url"],
        purged_at=purged_at.isoformat() if purged_at else None,
    )


def _row_out(row: asyncpg.Record) -> UnderlyingRowOut:
    confirmed_at = row["confirmed_at"]
    return UnderlyingRowOut(
        id=str(row["id"]),
        entity=row["entity"],
        payload=decode_payload(row["payload"]),
        method=row["method"],
        confirmed_at=confirmed_at.isoformat() if confirmed_at else None,
    )


@router.get("/slice", response_model=LineageSliceOut)
async def get_lineage_slice(
    kind: SliceKind,
    snapshot_date: date,
    bucket: str | None = Query(None),
    cut: str | None = Query(None),
    label: str | None = Query(None),
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> LineageSliceOut:
    if kind == "term_bucket":
        if not bucket:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "bucket is required for kind=term_bucket"
            )
        run_id = await lineage_slice_queries.find_run_id_for_term_bucket(
            conn, user_id=user_id, snapshot_date=snapshot_date, bucket=bucket
        )
    elif kind == "diversification":
        if not cut or not label:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "cut and label are required for kind=diversification",
            )
        run_id = await lineage_slice_queries.find_run_id_for_diversification_cut(
            conn, user_id=user_id, snapshot_date=snapshot_date, cut=cut, label=label
        )
    else:
        run_id = await lineage_slice_queries.find_run_id_for_net_worth(
            conn, user_id=user_id, snapshot_date=snapshot_date
        )

    if run_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no gold row for this slice")

    job_id = await lineage_slice_queries.find_job_id_for_run(
        conn, user_id=user_id, run_id=run_id
    )

    source_file_out: SourceFileOut | None = None
    rows_out: list[UnderlyingRowOut] = []
    if job_id is not None:
        job = await etl_jobs_queries.get_job(conn, user_id=user_id, job_id=job_id)
        if job is not None:
            bronze_file = await lineage_slice_queries.get_bronze_file(
                conn, user_id=user_id, bronze_file_id=str(job["bronze_file_id"])
            )
            if bronze_file is not None:
                source_file_out = _source_file_out(bronze_file)

        staged = await staged_rows_queries.list_rows_for_job(
            conn, user_id=user_id, job_id=job_id
        )
        rows_out = [_row_out(row) for row in staged]

    return LineageSliceOut(
        kind=kind,
        run_id=run_id,
        job_id=job_id,
        source_file=source_file_out,
        rows=rows_out,
    )
