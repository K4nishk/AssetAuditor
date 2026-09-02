"""Manual-entry form routes: the no-PDF path (KCH-55 / AA-20).

Portfolio (ticker/shares/avg-cost, optional lots, optional Yahoo Finance
export import) and account-balance forms build the same
`worker.adapters.base.StagedRowDraft` shapes the CSV/PDF adapters do
(`app.domain.manual_entry`), but there is no bronze file to extract from —
parsing happens synchronously in this request. To keep one review/confirm
path for every entity that lands in silver (CLAUDE.md's provenance rule,
AA-17's parse-confirm screen), each submission still gets a real
`bronze_files` row (the submitted form JSON itself, as its own immutable
source blob) and an `etl_jobs` row, just minted directly at `needs_user`
instead of going through the async upload/worker queue. The frontend then
sends the user to `/staged/{job_id}` (AA-17) to review and confirm, exactly
like a parsed statement.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user_id
from app.db.pool import rls_connection
from app.db.queries import bronze_files, etl_jobs, staged_rows
from app.domain.manual_entry import (
    AccountBalanceInput,
    AccountInput,
    LotInput,
    ManualEntryValidationError,
    PortfolioEntryInput,
    build_account_balance_drafts,
    build_portfolio_drafts,
    build_portfolio_drafts_from_yahoo,
)
from app.uploads.blob import BlobUploadError, bronze_pathname, get_blob_storage
from worker.adapters.base import AdapterParseError, StagedRowDraft
from worker.adapters.yahoo_finance import parse_lots as parse_yahoo_finance_lots
from worker.lineage import LineageEmitter, new_run_id

router = APIRouter(prefix="/api/manual-entry", tags=["manual-entry"])


async def _conn(user_id: str = Depends(get_current_user_id)) -> AsyncIterator[asyncpg.Connection]:
    async with rls_connection(user_id) as conn:
        yield conn


class AccountField(BaseModel):
    institution: str = Field(min_length=1)
    account_type: str = Field(min_length=1)
    account_number: str = Field(min_length=1)
    currency: str = "CAD"


class LotField(BaseModel):
    quantity: Decimal
    unit_cost: Decimal | None = None
    currency: str | None = None
    acquired_at: str | None = None
    vested: bool | None = None


class PortfolioEntryRequest(BaseModel):
    account: AccountField
    ticker: str = Field(min_length=1)
    quantity: Decimal
    avg_cost: Decimal | None = None
    currency: str = "CAD"
    lots: list[LotField] = Field(default_factory=list)


class YahooImportRequest(BaseModel):
    account: AccountField
    csv_text: str = Field(min_length=1)
    currency: str = "CAD"


class AccountBalanceRequest(BaseModel):
    account: AccountField
    balance: Decimal
    currency: str = "CAD"


class ManualEntryResponse(BaseModel):
    job_id: str
    status: str
    row_count: int


def _account_input(field: AccountField) -> AccountInput:
    return AccountInput(
        institution=field.institution,
        account_type=field.account_type,
        account_number=field.account_number,
        currency=field.currency,
    )


async def _stage_manual_entry(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    institution: str,
    source: str,
    body: BaseModel,
    drafts: list[StagedRowDraft],
    now: datetime,
) -> ManualEntryResponse:
    """Persist the submitted form itself as a bronze row (this path's only
    source document), stage every draft against a fresh `needs_user` job, and
    emit the lineage pair CLAUDE.md's provenance rule requires for every
    silver-bound write."""
    content = json.dumps(
        {"source": source, "submitted_at": now.isoformat(), "form": body.model_dump(mode="python")},
        default=str,
        sort_keys=True,
    ).encode("utf-8")
    sha256_hex = hashlib.sha256(content).hexdigest()

    pathname = bronze_pathname(user_id, sha256_hex)
    try:
        blob_url = await asyncio.to_thread(
            get_blob_storage().put, pathname, content, "application/json"
        )
    except BlobUploadError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "failed to store manual entry"
        ) from exc

    inserted = await bronze_files.insert_bronze_file(
        conn,
        user_id=user_id,
        sha256_hex=sha256_hex,
        institution=institution,
        period=None,
        blob_url=blob_url,
    )
    if inserted is None:
        existing = await bronze_files.find_by_sha256(conn, user_id=user_id, sha256_hex=sha256_hex)
        if existing is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "bronze file vanished after insert conflict"
            )
        bronze_file_id = str(existing["id"])
    else:
        bronze_file_id = str(inserted["id"])

    job = await etl_jobs.insert_needs_user_job(
        conn, user_id=user_id, bronze_file_id=bronze_file_id
    )

    run_id = new_run_id()
    emitter = LineageEmitter(conn, user_id=user_id, job_id=str(job["id"]), run_id=run_id)
    await emitter.start("manual_entry", facets={"source": source, "row_count": len(drafts)})

    for draft in drafts:
        await staged_rows.insert_draft(
            conn,
            user_id=user_id,
            job_id=str(job["id"]),
            entity=draft.entity,
            payload=draft.payload,
            confidence=draft.confidence,
            method=draft.method,
        )

    await emitter.complete("manual_entry", facets={"row_count": len(drafts)})

    return ManualEntryResponse(job_id=str(job["id"]), status=job["status"], row_count=len(drafts))


@router.post("/portfolio", response_model=ManualEntryResponse)
async def submit_portfolio_entry(
    body: PortfolioEntryRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ManualEntryResponse:
    try:
        drafts = build_portfolio_drafts(
            PortfolioEntryInput(
                account=_account_input(body.account),
                ticker=body.ticker,
                quantity=body.quantity,
                avg_cost=body.avg_cost,
                currency=body.currency,
                lots=[
                    LotInput(
                        quantity=lot.quantity,
                        unit_cost=lot.unit_cost,
                        currency=lot.currency,
                        acquired_at=lot.acquired_at,
                        vested=lot.vested,
                    )
                    for lot in body.lots
                ],
            )
        )
    except ManualEntryValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return await _stage_manual_entry(
        conn,
        user_id=user_id,
        institution=body.account.institution,
        source="manual_entry_portfolio",
        body=body,
        drafts=drafts,
        now=datetime.now(UTC),
    )


@router.post("/portfolio/yahoo-import", response_model=ManualEntryResponse)
async def import_yahoo_finance_portfolio(
    body: YahooImportRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ManualEntryResponse:
    try:
        lots = parse_yahoo_finance_lots(body.csv_text.encode("utf-8"))
    except AdapterParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    try:
        drafts = build_portfolio_drafts_from_yahoo(
            _account_input(body.account), lots, currency=body.currency
        )
    except ManualEntryValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return await _stage_manual_entry(
        conn,
        user_id=user_id,
        institution=body.account.institution,
        source="manual_entry_yahoo_import",
        body=body,
        drafts=drafts,
        now=datetime.now(UTC),
    )


@router.post("/account-balance", response_model=ManualEntryResponse)
async def submit_account_balance(
    body: AccountBalanceRequest,
    user_id: str = Depends(get_current_user_id),
    conn: asyncpg.Connection = Depends(_conn),
) -> ManualEntryResponse:
    now = datetime.now(UTC)
    try:
        drafts = build_account_balance_drafts(
            AccountBalanceInput(
                account=_account_input(body.account), balance=body.balance, currency=body.currency
            ),
            occurred_at=now,
        )
    except ManualEntryValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return await _stage_manual_entry(
        conn,
        user_id=user_id,
        institution=body.account.institution,
        source="manual_entry_account_balance",
        body=body,
        drafts=drafts,
        now=now,
    )
