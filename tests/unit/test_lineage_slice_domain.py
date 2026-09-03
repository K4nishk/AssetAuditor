"""Unit tests for `app.domain.lineage_slice.describe_source_file` (KCH-60 / AA-23)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.lineage_slice import describe_source_file


def test_live_bronze_file_is_not_purged():
    status = describe_source_file(purged_at=None, blob_url="https://blob.example/x")

    assert status.is_purged is False


def test_purged_at_set_marks_the_file_purged():
    status = describe_source_file(
        purged_at=datetime(2026, 6, 1, tzinfo=UTC), blob_url="https://blob.example/x"
    )

    assert status.is_purged is True


def test_blank_blob_url_alone_marks_the_file_purged():
    """`worker.retention.sweep_bronze_files` sets `purged_at` and blanks
    `blob_url` together, but this checks both independently so a state
    where one lags the other still reads as purged rather than live."""
    status = describe_source_file(purged_at=None, blob_url="")

    assert status.is_purged is True
