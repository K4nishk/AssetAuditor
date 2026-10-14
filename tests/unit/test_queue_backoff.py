"""Backoff schedule for waiting jobs (AA-63, #46; owner decision R4.1). Pure, no DB."""

import pytest

from worker.queue import WAIT_REASONS, backoff_minutes, defer_job


class _UntouchableConn:
    """Fails the test if `defer_job` reaches the DB before validating its input."""

    def __getattr__(self, name: str):
        raise AssertionError(f"defer_job touched the connection ({name}) before validating")


def test_backoff_doubles_from_one_minute_then_holds_at_sixty() -> None:
    assert [backoff_minutes(n) for n in range(10)] == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]


def test_a_negative_defer_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="defer_count"):
        backoff_minutes(-1)


@pytest.mark.asyncio
async def test_defer_rejects_an_unknown_reason_before_touching_the_db() -> None:
    with pytest.raises(ValueError, match="wait reason"):
        await defer_job(
            _UntouchableConn(),
            job_id="j",
            claimed_by="w",
            expected_status="claimed",
            reason="bogus",
        )


@pytest.mark.asyncio
async def test_defer_only_from_claimed_or_parsing() -> None:
    reason = next(iter(WAIT_REASONS))
    for status in ("pending", "needs_user", "done", "failed"):
        with pytest.raises(ValueError, match="cannot defer"):
            await defer_job(
                _UntouchableConn(),
                job_id="j",
                claimed_by="w",
                expected_status=status,
                reason=reason,
            )
