"""Queued-upload UX derived from `worker_heartbeat` (AA-34).

Pure — no I/O — mirrors `app.domain.rooms.engine`'s shape. The caller (an
API route) reads the single `worker_heartbeat` row and passes its
`last_beat_at` in; this module only decides what to tell the user.

Two different staleness windows are intentional, not a copy-paste of the
same constant:

- `UX_STALE_THRESHOLD` answers "is the worker online right now" for the
  upload-status message. It tracks `worker/main.py`'s
  `HEARTBEAT_INTERVAL_SECONDS` (30s) closely so the message flips promptly
  when the box goes down.
- `ALERT_STALE_THRESHOLD` is the paging threshold from
  docs/vault/30-architecture/Observability.md ("worker heartbeat stale >2h
  while jobs queued") — deliberately much longer so a brief blip doesn't
  wake anyone up. `worker/observability/stale_while_queued_alert.yaml`
  encodes the same window in PromQL so the UI and the alert never disagree
  about what "stale" means at each layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

UX_STALE_THRESHOLD = timedelta(seconds=90)
ALERT_STALE_THRESHOLD = timedelta(hours=2)

QUEUED_ONLINE_MESSAGE = "queued — your worker will process it shortly"
QUEUED_OFFLINE_MESSAGE = "queued — will process when your worker is online"

# AA-63 (#46): a waiting job (pending plus `etl_jobs.wait_reason`) says why.
# Fixed text per reason — never values from the statement — and the keys must
# match migration 0005's check constraint and `worker.queue.WAIT_REASONS`.
WAIT_REASON_MESSAGES = {
    "local_not_ready": (
        "waiting — your local model is still starting; "
        "this statement stays on your Box until it's ready"
    ),
    "local_unavailable": (
        "waiting — your local model isn't reachable; this statement stays on your Box until it is"
    ),
    "not_codable": (
        "waiting — this statement couldn't be coded safely for the fallback, "
        "so it waits for your local model"
    ),
    "check_failed": (
        "waiting — the pre-send check stopped the fallback, "
        "so this statement waits for your local model"
    ),
    "egress_unavailable": (
        "waiting — neither your local model nor the fallback is reachable; retrying with backoff"
    ),
    "coded_result_invalid": (
        "waiting — the fallback's answer didn't decode cleanly, "
        "so this statement waits for your local model"
    ),
}


@dataclass(frozen=True)
class QueueState:
    worker_online: bool
    message: str


def describe_queue_state(
    *,
    last_beat_at: datetime | None,
    now: datetime,
    threshold: timedelta = UX_STALE_THRESHOLD,
    wait_reason: str | None = None,
) -> QueueState:
    """Describe a still-`pending` job's queue state for upload-status UX.

    An offline worker wins over a wait reason: nothing moves until it's back.
    """
    if last_beat_at is None or (now - last_beat_at) > threshold:
        return QueueState(worker_online=False, message=QUEUED_OFFLINE_MESSAGE)
    if wait_reason is not None:
        return QueueState(worker_online=True, message=WAIT_REASON_MESSAGES[wait_reason])
    return QueueState(worker_online=True, message=QUEUED_ONLINE_MESSAGE)
