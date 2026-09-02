"""rebuild_gold(user_id): snapshots, buckets, cuts, room ledger, CSV (KCH-53 / AA-18).

Orchestration only — `app.db.queries.gold` owns the SQL, `app.domain.gold`
and `app.domain.rooms.links` own the pure math/derivation. `rebuild_gold`
runs on whichever connection its caller passes (RLS-scoped from the API, or
the worker's service_role connection from the job loop — both work, same as
`app.db.queries.silver.write_confirmed_rows`) and is a full re-derivation
from current silver state every time it runs (`docs/vault/30-architecture/
ETL-Pipeline.md`: "Gold is fully rebuildable ... the recovery story").

**Not wired into a caller in this issue** — same deferred-integration
convention `app/routes/staged.py` and `worker/main.py`'s job loop already
follow for AA-14/15/16's extraction tiers. Whichever issue wires the
confirm flow or the job loop to call this also decides where the
`LineageEmitter` and `BlobStorage` it needs come from.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import asyncpg
import pyarrow as pa
import pyarrow.parquet as pq

from app.db.queries import gold as gold_queries
from app.domain.gold import GoldTotals, compute_gold_totals
from app.domain.rooms.links import derive_contribution_room_events
from app.uploads.blob import BlobStorage
from worker.lineage import LineageEmitter

_SILVER_ENTITIES = ("accounts", "holdings", "lots", "transactions", "liabilities")


@dataclass(frozen=True)
class GoldRebuildResult:
    totals: GoldTotals
    room_events_written: int
    silver_paths: dict[str, str] | None
    gold_csv_paths: dict[str, str] | None


async def rebuild_gold(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    snapshot_date: date,
    lineage: LineageEmitter,
    blob: BlobStorage | None = None,
) -> GoldRebuildResult:
    """Recompute this user's gold layer from current confirmed silver rows.

    `blob` is optional: when omitted, the Postgres gold tables and the
    derived room-contribution ledger are still rebuilt, just without the
    silver-parquet/gold-CSV export step (useful for callers/tests that don't
    have a `BLOB_READ_WRITE_TOKEN` wired, same escape hatch
    `app.uploads.blob.get_blob_storage` requires the env var for).
    """
    await lineage.start("gold_rebuild")
    try:
        async with conn.transaction():
            silver = await gold_queries.fetch_silver_for_valuation(conn, user_id=user_id)
            assets = gold_queries.holding_valuations(
                silver
            ) + gold_queries.cash_balances_by_account(silver.cash_transactions)
            totals = compute_gold_totals(assets, silver.liabilities)

            await gold_queries.write_gold_snapshot(
                conn,
                user_id=user_id,
                snapshot_date=snapshot_date,
                run_id=lineage.run_id,
                total_assets_cad=totals.total_assets_cad,
                total_liabilities_cad=totals.total_liabilities_cad,
                net_worth_cad=totals.net_worth_cad,
                term_buckets=totals.term_buckets,
                diversification_cuts=totals.diversification_cuts,
            )

            contribution_txns = await gold_queries.fetch_contribution_transactions(
                conn, user_id=user_id
            )
            derived_events = derive_contribution_room_events(contribution_txns)
            room_events_written = await gold_queries.replace_derived_room_events(
                conn, user_id=user_id, events=derived_events
            )

            entity_rows = (
                await gold_queries.fetch_silver_entity_rows(conn, user_id=user_id)
                if blob is not None
                else None
            )

        silver_paths: dict[str, str] | None = None
        gold_csv_paths: dict[str, str] | None = None
        if blob is not None and entity_rows is not None:
            silver_paths = write_silver_parquet(entity_rows, blob=blob, user_id=user_id)
            gold_csv_paths = write_gold_csv(
                totals, blob=blob, user_id=user_id, snapshot_date=snapshot_date
            )
    except Exception as exc:
        await lineage.fail("gold_rebuild", error=str(exc))
        raise

    await lineage.complete(
        "gold_rebuild",
        facets={
            "total_assets_cad": str(totals.total_assets_cad),
            "total_liabilities_cad": str(totals.total_liabilities_cad),
            "net_worth_cad": str(totals.net_worth_cad),
            "room_events_written": room_events_written,
        },
    )

    return GoldRebuildResult(
        totals=totals,
        room_events_written=room_events_written,
        silver_paths=silver_paths,
        gold_csv_paths=gold_csv_paths,
    )


def silver_pathname(user_id: str, entity: str) -> str:
    return f"silver/{user_id}/{entity}.parquet"


def gold_pathname(user_id: str, table: str) -> str:
    return f"gold/{user_id}/{table}.csv"


def _stringify(value: Any) -> str | None:
    """Every parquet column is written as text rather than an inferred
    Arrow type: a `Decimal` money/quantity value must never round-trip
    through anything lossy (CLAUDE.md hard rule #4), and mixing precisions
    in one column (crypto's 8dp beside CAD's 2dp) would otherwise trip
    pyarrow's single-scale `decimal128` inference. A reader that needs the
    typed value back runs it through `Decimal`/`datetime.fromisoformat`,
    same pattern `app.domain.staged_rows.decode_payload` documents for jsonb."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _rows_to_table(rows: list[asyncpg.Record]) -> pa.Table:
    if not rows:
        return pa.table({})
    columns = list(rows[0].keys())
    return pa.table({column: [_stringify(row[column]) for row in rows] for column in columns})


def write_silver_parquet(
    entity_rows: gold_queries.SilverEntityRows, *, blob: BlobStorage, user_id: str
) -> dict[str, str]:
    """Canonical parquet snapshot of every confirmed silver row.

    Full current state per entity, not partitioned by upload period: silver
    rows carry no period/bronze-file linkage to partition by (a statement
    period lives only on `bronze_files`, decoupled from individual silver
    rows once written) — a full-snapshot export is the pragmatic MVP shape
    `docs/vault/30-architecture/ETL-Pipeline.md`'s
    `silver/{user_id}/{entity}/{period}.parquet` sketch simplifies to.
    """
    paths: dict[str, str] = {}
    for entity in _SILVER_ENTITIES:
        table = _rows_to_table(getattr(entity_rows, entity))
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        pathname = silver_pathname(user_id, entity)
        blob.put(pathname, buffer.getvalue(), "application/octet-stream")
        paths[entity] = pathname
    return paths


def write_gold_csv(
    totals: GoldTotals, *, blob: BlobStorage, user_id: str, snapshot_date: date
) -> dict[str, str]:
    paths: dict[str, str] = {}

    snapshot_buf = io.StringIO()
    writer = csv.writer(snapshot_buf)
    writer.writerow(
        ["snapshot_date", "total_assets_cad", "total_liabilities_cad", "net_worth_cad"]
    )
    writer.writerow(
        [
            snapshot_date.isoformat(),
            str(totals.total_assets_cad),
            str(totals.total_liabilities_cad),
            str(totals.net_worth_cad),
        ]
    )
    paths["networth_snapshot"] = _put_csv(blob, user_id, "networth_snapshot", snapshot_buf)

    buckets_buf = io.StringIO()
    writer = csv.writer(buckets_buf)
    writer.writerow(["snapshot_date", "bucket", "amount_cad"])
    for bucket, amount in totals.term_buckets.items():
        writer.writerow([snapshot_date.isoformat(), bucket, str(amount)])
    paths["term_buckets"] = _put_csv(blob, user_id, "term_buckets", buckets_buf)

    cuts_buf = io.StringIO()
    writer = csv.writer(cuts_buf)
    writer.writerow(["snapshot_date", "cut", "label", "amount_cad"])
    for (cut, label), amount in totals.diversification_cuts.items():
        writer.writerow([snapshot_date.isoformat(), cut, label, str(amount)])
    paths["diversification_cuts"] = _put_csv(blob, user_id, "diversification_cuts", cuts_buf)

    return paths


def _put_csv(blob: BlobStorage, user_id: str, table: str, buffer: io.StringIO) -> str:
    pathname = gold_pathname(user_id, table)
    blob.put(pathname, buffer.getvalue().encode("utf-8"), "text/csv")
    return pathname
