"""Unit tests for `app.domain.worker_status` (KCH-57 / AA-34).

Pure — no DB, no worker process — matches tests/unit/test_rooms_engine.py's
approach for the other pure-domain module.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.worker_status import (
    QUEUED_OFFLINE_MESSAGE,
    QUEUED_ONLINE_MESSAGE,
    UX_STALE_THRESHOLD,
    describe_queue_state,
)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def test_describe_queue_state_is_online_for_a_fresh_heartbeat():
    result = describe_queue_state(last_beat_at=NOW - timedelta(seconds=10), now=NOW)

    assert result.worker_online is True
    assert result.message == QUEUED_ONLINE_MESSAGE


def test_describe_queue_state_is_offline_once_past_the_threshold():
    result = describe_queue_state(last_beat_at=NOW - timedelta(seconds=91), now=NOW)

    assert result.worker_online is False
    assert result.message == QUEUED_OFFLINE_MESSAGE


def test_describe_queue_state_is_online_exactly_at_the_threshold_boundary():
    result = describe_queue_state(last_beat_at=NOW - UX_STALE_THRESHOLD, now=NOW)

    assert result.worker_online is True


def test_describe_queue_state_is_offline_when_no_heartbeat_row_exists_yet():
    result = describe_queue_state(last_beat_at=None, now=NOW)

    assert result.worker_online is False
    assert result.message == QUEUED_OFFLINE_MESSAGE


def test_describe_queue_state_honours_a_custom_threshold():
    result = describe_queue_state(
        last_beat_at=NOW - timedelta(minutes=5), now=NOW, threshold=timedelta(minutes=10)
    )

    assert result.worker_online is True
