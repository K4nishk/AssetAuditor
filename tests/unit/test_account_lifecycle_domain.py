"""Unit tests for `app.domain.account_lifecycle.is_reauth_fresh` (KCH-45 / AA-10)."""

from __future__ import annotations

from datetime import timedelta

from app.domain.account_lifecycle import REAUTH_MAX_AGE, is_reauth_fresh

NOW = 1_800_000_000.0


def test_a_token_issued_just_now_is_fresh():
    assert is_reauth_fresh(NOW, now_epoch_seconds=NOW) is True


def test_a_token_issued_within_the_max_age_is_fresh():
    issued_at = NOW - REAUTH_MAX_AGE.total_seconds() + 1
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW) is True


def test_a_token_issued_exactly_at_the_max_age_boundary_is_fresh():
    issued_at = NOW - REAUTH_MAX_AGE.total_seconds()
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW) is True


def test_a_token_issued_before_the_max_age_is_stale():
    issued_at = NOW - REAUTH_MAX_AGE.total_seconds() - 1
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW) is False


def test_a_token_from_a_much_older_session_is_stale():
    issued_at = NOW - timedelta(days=1).total_seconds()
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW) is False


def test_a_slightly_future_iat_within_skew_tolerance_is_fresh():
    assert is_reauth_fresh(NOW + 3, now_epoch_seconds=NOW) is True


def test_a_far_future_iat_beyond_skew_tolerance_is_stale():
    assert is_reauth_fresh(NOW + 30, now_epoch_seconds=NOW) is False


def test_a_custom_max_age_is_respected():
    issued_at = NOW - 90
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW, max_age=timedelta(minutes=1)) is False
    assert is_reauth_fresh(issued_at, now_epoch_seconds=NOW, max_age=timedelta(minutes=2)) is True
