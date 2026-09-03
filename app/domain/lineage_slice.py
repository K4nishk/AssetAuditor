"""Pure shaping for the drill-down panel (KCH-60 / AA-23).

`app.routes.lineage` walks gold row -> run_id -> `lineage_events.job_id` ->
bronze_files/staged_rows (`worker/lineage.py`'s module docstring: "That
shared run_id is what AA-23's drill-down panel follows backward from a gold
row to its bronze file (or purge tombstone)"). This module holds the one
piece of branching logic in that walk — whether a bronze file is still live
or has been tombstoned by AA-19's retention sweeper — so the route stays a
thin HTTP/SQL shim, same domain/IO split as `app.domain.dashboard`/
`app.domain.gold`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SourceFileStatus:
    is_purged: bool


def describe_source_file(*, purged_at: datetime | None, blob_url: str) -> SourceFileStatus:
    """`worker.retention.sweep_bronze_files` tombstones a purged row by
    setting `purged_at` and blanking `blob_url` to `''` together, in the
    same update (`_MARK_BRONZE_PURGED_SQL`) — either signal alone is enough
    to detect it, so this checks both rather than trusting one column to
    never lag the other.
    """
    return SourceFileStatus(is_purged=purged_at is not None or blob_url == "")


__all__ = ["SourceFileStatus", "describe_source_file"]
