"""Signed upload URLs + bronze registry + job status routes (AA-11).

Flow: `POST /api/uploads` validates the declared sha256/size/content-type,
checks `bronze_files` for an existing (user, sha256) row, and — for a new
file — returns a signed, expiring upload URL (`app.uploads.signing`) instead
of a bronze row, because `bronze_files.blob_url` is `not null` and no row can
exist before the bytes do. The client then `PUT`s the raw bytes to that URL,
which re-validates them (now against the real content, not just the
declaration), relays them to Vercel Blob, inserts the bronze row, and enqueues
the `etl_jobs` row the worker will later claim (`worker.queue.claim_next_job`).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import bronze_files, etl_jobs
from app.uploads.blob import BlobUploadError, bronze_pathname, get_blob_storage
from app.uploads.signing import (
    DEFAULT_TTL_SECONDS,
    InvalidUploadToken,
    create_upload_token,
    verify_upload_token,
)
from app.uploads.validation import (
    MAX_UPLOAD_SIZE_BYTES,
    UploadDeclaration,
    UploadRejected,
    validate_declaration,
    validate_received_bytes,
)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _upload_secret() -> bytes:
    return os.environ["UPLOAD_TOKEN_SECRET"].encode("utf-8")


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    # `user_id` resolves once per request — FastAPI caches `Depends` results
    # by callable, so this reuses the same JWT verification a route's own
    # `Depends(get_current_user_id)` parameter triggers, not a second one.
    async with rls_connection(user_id) as conn:
        yield conn


class RegisterUploadRequest(BaseModel):
    sha256_hex: str
    size_bytes: int
    content_type: str
    institution: str | None = None
    period: str | None = None


class RegisterUploadResponse(BaseModel):
    status: str  # "pending_upload" | "duplicate" | "queued"
    bronze_file_id: str | None = None
    upload_url: str | None = None
    expires_in_seconds: int | None = None


class UploadStatusResponse(BaseModel):
    bronze_file_id: str
    status: str
    error: str | None = None


@router.post("", response_model=RegisterUploadResponse)
async def register_upload(
    body: RegisterUploadRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> RegisterUploadResponse:
    try:
        validate_declaration(
            UploadDeclaration(
                sha256_hex=body.sha256_hex,
                size_bytes=body.size_bytes,
                content_type=body.content_type,
            )
        )
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.reason) from exc

    existing = await bronze_files.find_by_sha256(
        conn, user_id=user_id, sha256_hex=body.sha256_hex
    )
    if existing is not None:
        return RegisterUploadResponse(status="duplicate", bronze_file_id=str(existing["id"]))

    token = create_upload_token(
        user_id=user_id,
        sha256_hex=body.sha256_hex,
        institution=body.institution,
        period=body.period,
        secret=_upload_secret(),
    )
    return RegisterUploadResponse(
        status="pending_upload",
        upload_url=f"/api/uploads/blob?token={token}",
        expires_in_seconds=int(DEFAULT_TTL_SECONDS),
    )


@router.put("/blob", response_model=RegisterUploadResponse)
async def upload_blob(
    request: Request,
    token: str,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> RegisterUploadResponse:
    try:
        payload = verify_upload_token(token, secret=_upload_secret())
    except InvalidUploadToken as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc

    if payload.user_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "token does not belong to this user")

    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"upload exceeds cap of {MAX_UPLOAD_SIZE_BYTES} bytes",
        )

    content_type = request.headers.get("content-type", "")
    data = await request.body()

    try:
        validate_received_bytes(declared_content_type=content_type, data=data)
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.reason) from exc

    if hashlib.sha256(data).hexdigest() != payload.sha256_hex:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "uploaded bytes do not match the sha256 declared at /uploads",
        )

    pathname = bronze_pathname(user_id, payload.sha256_hex)
    try:
        blob_url = get_blob_storage().put(pathname, data, content_type)
    except BlobUploadError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "failed to store upload") from exc

    inserted = await bronze_files.insert_bronze_file(
        conn,
        user_id=user_id,
        sha256_hex=payload.sha256_hex,
        institution=payload.institution,
        period=payload.period,
        blob_url=blob_url,
    )
    if inserted is None:
        # Lost a race with a concurrent identical upload — the bytes are
        # already safely in Blob under a deterministic pathname either way.
        existing = await bronze_files.find_by_sha256(
            conn, user_id=user_id, sha256_hex=payload.sha256_hex
        )
        if existing is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "bronze file vanished after insert conflict"
            )
        return RegisterUploadResponse(status="duplicate", bronze_file_id=str(existing["id"]))

    job = await etl_jobs.enqueue_job(conn, user_id=user_id, bronze_file_id=str(inserted["id"]))
    return RegisterUploadResponse(
        status="queued" if job is not None else "duplicate",
        bronze_file_id=str(inserted["id"]),
    )


@router.get("/{bronze_file_id}/status", response_model=UploadStatusResponse)
async def upload_status(
    bronze_file_id: str,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> UploadStatusResponse:
    job = await etl_jobs.get_job_status_for_bronze_file(
        conn, user_id=user_id, bronze_file_id=bronze_file_id
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no upload found for this bronze file")
    return UploadStatusResponse(
        bronze_file_id=str(job["bronze_file_id"]), status=job["status"], error=job["error"]
    )
