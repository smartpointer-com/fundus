"""The Source interface — a pluggable data-source connector.

A Source enumerates the artifacts of one corpus (a mail store, a chat database, a
file tree) incrementally, and can list the ids currently alive for deletion
reconciliation. New source types implement this Protocol and register in
``sources/registry.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from fundus.models import Cursor, FileStat, SourceItem


class Source(Protocol):
    name: str  # configured instance name
    type: str  # connector type identifier

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        """Yield artifacts created/modified since ``cursor`` (all of them if None)."""
        ...

    def live_ids(self) -> Iterator[str]:
        """Yield every native_id currently present (for ``--full`` reconciliation)."""
        ...

    def current_cursor(self) -> Cursor:
        """The cursor to persist after a successful pass."""
        ...


@runtime_checkable
class TreeSource(Source, Protocol):
    """A source backed by a mutable file tree (vs. an append-only mail/chat store).

    Implementing this opts the source into rsync-style ``--full`` reconciliation:
    the pipeline reads every live file's fingerprint, diffs it against the indexed
    manifest, and re-indexes only what changed — independent of the mtime cursor, so
    an edit is caught even when its mtime regressed or never moved.
    """

    def fingerprints(self) -> Iterator[tuple[str, FileStat]]:
        """Yield ``(native_id, FileStat)`` for every live file — stat only, no byte reads."""
        ...

    def load(self, native_id: str) -> SourceItem | None:
        """Materialize one file into a SourceItem (reading its bytes); None if unreadable/skipped."""
        ...
