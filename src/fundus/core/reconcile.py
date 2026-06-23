"""Deletion & change reconciliation between a source and the index.

Two mechanisms:
- ``live_parent_ids`` feeds the append-only set-difference deletion used for mail/chat
  sources (their artifacts are immutable, so only additions and deletions occur).
- ``diff_tree`` powers rsync-style reconciliation for file-tree sources: compare the
  live tree's fingerprints against the indexed manifest to find what to (re)index and
  what to drop — catching edits the mtime cursor cannot observe.
"""

from __future__ import annotations

from fundus.core.ids import parent_id
from fundus.models import FileStat
from fundus.sources.base import Source

# Tolerance for mtime equality: filesystem timestamps survive a JSON round-trip with
# tiny float wobble, so compare within a microsecond rather than for exact equality.
_MTIME_EPS = 1e-6


def live_parent_ids(source: Source) -> set[str]:
    """The set of parent ids currently alive in a source (for set-difference deletion)."""
    return {parent_id(source.name, native_id) for native_id in source.live_ids()}


def _same(a: FileStat | None, b: FileStat) -> bool:
    return a is not None and a.size == b.size and abs(a.mtime - b.mtime) <= _MTIME_EPS


def diff_tree(
    disk: dict[str, FileStat], indexed: dict[str, FileStat]
) -> tuple[set[str], set[str]]:
    """Diff a live file tree against the indexed manifest (both ``native_id -> FileStat``).

    Returns ``(changed, removed)``: ``changed`` are native_ids new or whose size/mtime
    differs (to re-index); ``removed`` are indexed native_ids no longer on disk (to delete).
    """
    changed = {nid for nid, stat in disk.items() if not _same(indexed.get(nid), stat)}
    removed = set(indexed) - set(disk)
    return changed, removed
