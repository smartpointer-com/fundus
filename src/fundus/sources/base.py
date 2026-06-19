"""The Source interface — a pluggable data-source connector.

A Source enumerates the artifacts of one corpus (a mail store, a chat database, a
file tree) incrementally, and can list the ids currently alive for deletion
reconciliation. New source types implement this Protocol and register in
``sources/registry.py``.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from fundus.models import Cursor, SourceItem


@runtime_checkable
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
