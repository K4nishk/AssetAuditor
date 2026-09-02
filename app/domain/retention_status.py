"""Retention-sweeper staleness check (AA-19).

Pure — no I/O — same shape as `app.domain.worker_status`. Threshold mirrors
docs/vault/30-architecture/Observability.md's
`retention_sweeper_last_success_timestamp (alert if stale > 26h)` entry, and
worker/observability/retention_sweeper_stale_alert.yaml encodes the same
window in PromQL so this module and the alert never disagree about what
"stale" means. mvp.md's AA-19 spec: "Treat sweeper-stale as a privacy
incident" — unlike app.domain.worker_status's queue-offline case, staleness
here is never routine ops noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

ALERT_STALE_THRESHOLD = timedelta(hours=26)

STALE_MESSAGE = "retention sweeper stale — treat as a privacy incident"
HEALTHY_MESSAGE = "retention sweeper healthy"


@dataclass(frozen=True)
class SweeperState:
    is_stale: bool
    message: str


def describe_sweeper_state(
    *,
    last_success_at: datetime | None,
    now: datetime,
    threshold: timedelta = ALERT_STALE_THRESHOLD,
) -> SweeperState:
    """Describe whether the retention sweeper's last success is within threshold."""
    if last_success_at is None or (now - last_success_at) > threshold:
        return SweeperState(is_stale=True, message=STALE_MESSAGE)
    return SweeperState(is_stale=False, message=HEALTHY_MESSAGE)
