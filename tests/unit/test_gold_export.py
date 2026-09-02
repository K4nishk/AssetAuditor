"""Silver parquet + gold CSV export (KCH-53 / AA-18).

No real Vercel Blob call — a fake `BlobStorage` records every `put()` in
memory, same pattern `tests/unit/test_blob_client.py` uses for the bronze
upload path. `asyncpg.Record` isn't constructible outside a live connection,
so entity rows are plain `dict`s here: `worker.gold._rows_to_table` only
ever calls `.keys()`/`__getitem__` on a row, which a `dict` satisfies
identically.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pyarrow.parquet as pq

from app.db.queries.gold import SilverEntityRows
from app.domain.gold import GoldTotals
from worker.gold import gold_pathname, silver_pathname, write_gold_csv, write_silver_parquet


class FakeBlobStorage:
    def __init__(self) -> None:
        self.puts: dict[str, tuple[bytes, str]] = {}

    def put(self, pathname: str, data: bytes, content_type: str) -> str:
        self.puts[pathname] = (data, content_type)
        return f"https://blob.example/{pathname}"


def test_write_silver_parquet_writes_one_file_per_entity() -> None:
    entity_rows = SilverEntityRows(
        accounts=[{"id": "a1", "institution": "scotia", "currency": "CAD"}],
        holdings=[{"id": "h1", "ticker": "AAPL", "quantity": Decimal("10")}],
        lots=[],
        transactions=[
            {"id": "t1", "kind": "credit", "amount": Decimal("3450.00"), "description": None}
        ],
        liabilities=[],
    )
    blob = FakeBlobStorage()

    paths = write_silver_parquet(entity_rows, blob=blob, user_id="user-1")

    assert paths == {
        "accounts": silver_pathname("user-1", "accounts"),
        "holdings": silver_pathname("user-1", "holdings"),
        "lots": silver_pathname("user-1", "lots"),
        "transactions": silver_pathname("user-1", "transactions"),
        "liabilities": silver_pathname("user-1", "liabilities"),
    }
    assert set(blob.puts) == set(paths.values())

    data, content_type = blob.puts[silver_pathname("user-1", "transactions")]
    assert content_type == "application/octet-stream"
    table = pq.read_table(io.BytesIO(data))
    row = table.to_pylist()[0]
    assert row["amount"] == "3450.00"  # Decimal round-trips as its exact string form
    assert row["description"] is None


def test_write_silver_parquet_handles_empty_entities() -> None:
    entity_rows = SilverEntityRows(
        accounts=[], holdings=[], lots=[], transactions=[], liabilities=[]
    )
    blob = FakeBlobStorage()

    paths = write_silver_parquet(entity_rows, blob=blob, user_id="user-1")

    assert len(paths) == 5
    for data, _content_type in blob.puts.values():
        table = pq.read_table(io.BytesIO(data))
        assert table.num_rows == 0


def test_write_gold_csv_writes_three_tables() -> None:
    totals = GoldTotals(
        total_assets_cad=Decimal("12410"),
        total_liabilities_cad=Decimal("9800"),
        net_worth_cad=Decimal("2610"),
        term_buckets={"short_term": Decimal("4200"), "liabilities": Decimal("9800")},
        diversification_cuts={("institution", "scotia"): Decimal("4200")},
    )
    blob = FakeBlobStorage()

    paths = write_gold_csv(totals, blob=blob, user_id="user-1", snapshot_date=date(2026, 7, 31))

    assert paths == {
        "networth_snapshot": gold_pathname("user-1", "networth_snapshot"),
        "term_buckets": gold_pathname("user-1", "term_buckets"),
        "diversification_cuts": gold_pathname("user-1", "diversification_cuts"),
    }
    snapshot_csv, content_type = blob.puts[gold_pathname("user-1", "networth_snapshot")]
    assert content_type == "text/csv"
    assert b"2610" in snapshot_csv
    buckets_csv, _ = blob.puts[gold_pathname("user-1", "term_buckets")]
    assert b"short_term" in buckets_csv and b"4200" in buckets_csv
    cuts_csv, _ = blob.puts[gold_pathname("user-1", "diversification_cuts")]
    assert b"scotia" in cuts_csv
