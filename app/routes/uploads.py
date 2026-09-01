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

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import bronze_files, etl_jobs, worker_heartbeat
from app.domain.worker_status import describe_queue_state
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
    # Set only while status == "pending" — AA-34's "queued — will process
    # when your worker is online" UX. None once a worker has claimed the job
    # (worker_online/message stop being the relevant question at that point).
    worker_online: bool | None = None
    message: str | None = None


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
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "malformed content-length header"
            ) from exc
        if declared_size > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"upload exceeds cap of {MAX_UPLOAD_SIZE_BYTES} bytes",
            )

    content_type = request.headers.get("content-type", "")

    # Stream and check the running total rather than `await request.body()`
    # then check: a missing/understated content-length header would otherwise
    # let an arbitrarily large body be buffered fully into memory before the
    # cap is ever enforced.
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"upload exceeds cap of {MAX_UPLOAD_SIZE_BYTES} bytes",
            )
        chunks.append(chunk)
    data = b"".join(chunks)

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
        # `VercelBlobStorage.put` is a blocking `urllib` call — running it
        # in-thread would block the event loop for every other request this
        # process is serving for the duration of the upload.
        blob_url = await asyncio.to_thread(get_blob_storage().put, pathname, data, content_type)
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
    bronze_file_id: UUID,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> UploadStatusResponse:
    job = await etl_jobs.get_job_status_for_bronze_file(
        conn, user_id=user_id, bronze_file_id=str(bronze_file_id)
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no upload found for this bronze file")

    worker_online: bool | None = None
    message: str | None = None
    if job["status"] == "pending":
        heartbeat = await worker_heartbeat.get_latest_heartbeat(conn)
        queue_state = describe_queue_state(
            last_beat_at=heartbeat["last_beat_at"] if heartbeat is not None else None,
            now=datetime.now(UTC),
        )
        worker_online = queue_state.worker_online
        message = queue_state.message

    return UploadStatusResponse(
        bronze_file_id=str(job["bronze_file_id"]),
        status=job["status"],
        error=job["error"],
        worker_online=worker_online,
        message=message,
    )
