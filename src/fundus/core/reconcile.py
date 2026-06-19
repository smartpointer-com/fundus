"""Deletion reconciliation: translate a source's live ids into parent ids."""

from __future__ import annotations

from fundus.core.ids import parent_id
from fundus.sources.base import Source


def live_parent_ids(source: Source) -> set[str]:
    """The set of parent ids currently alive in a source (for set-difference deletion)."""
    return {parent_id(source.name, native_id) for native_id in source.live_ids()}
