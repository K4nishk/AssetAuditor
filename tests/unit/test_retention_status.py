"""Unit tests for `app.domain.retention_status` (KCH-54 / AA-19)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.retention_status import (
    ALERT_STALE_THRESHOLD,
    STALE_MESSAGE,
    describe_sweeper_state,
)

_NOW = datetime(2026, 9, 2, tzinfo=UTC)


def test_recent_success_is_not_stale():
    state = describe_sweeper_state(last_success_at=_NOW - timedelta(hours=1), now=_NOW)

    assert state.is_stale is False


def test_success_just_under_threshold_is_not_stale():
    state = describe_sweeper_state(
        last_success_at=_NOW - ALERT_STALE_THRESHOLD + timedelta(minutes=1), now=_NOW
    )

    assert state.is_stale is False


def test_success_past_threshold_is_stale():
    state = describe_sweeper_state(
        last_success_at=_NOW - ALERT_STALE_THRESHOLD - timedelta(minutes=1), now=_NOW
    )

    assert state.is_stale is True
    assert state.message == STALE_MESSAGE


def test_no_recorded_success_is_stale():
    state = describe_sweeper_state(last_success_at=None, now=_NOW)

    assert state.is_stale is True
