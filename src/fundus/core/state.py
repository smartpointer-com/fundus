"""Per-source incremental state (cursors).

A simple, proven pattern: one opaque cursor per source in a JSON file, written
atomically. Cross-process safety is provided by the run lock around indexing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from fundus.models import Cursor


@runtime_checkable
class StateStore(Protocol):
    def get_cursor(self, source: str) -> Cursor | None: ...

    def set_cursor(self, source: str, cursor: Cursor) -> None: ...


class JsonStateStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        data: dict[str, str] = json.loads(self._path.read_text() or "{}")
        return data

    def get_cursor(self, source: str) -> Cursor | None:
        return self._load().get(source)

    def set_cursor(self, source: str, cursor: Cursor) -> None:
        data = self._load()
        data[source] = cursor
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, self._path)
