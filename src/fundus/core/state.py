"""Per-source incremental state (cursors).

A simple, proven pattern: an atomic JSON file guarded by a file lock, storing one
opaque cursor per source. No database required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fundus.models import Cursor


@runtime_checkable
class StateStore(Protocol):
    def get_cursor(self, source: str) -> Cursor | None: ...

    def set_cursor(self, source: str, cursor: Cursor) -> None: ...
