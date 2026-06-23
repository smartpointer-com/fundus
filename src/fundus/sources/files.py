"""Filesystem connector: walk configured roots, emit changed files by mtime.

Each file becomes a ``file`` SourceItem carrying its bytes as a ``BlobPayload``;
the pipeline decides whether to extract (documents) or chunk directly (tabular).

Two change-detection modes share one tree walk:
- **Incremental** (`changed`): the cursor is the maximum mtime seen, so a run only
  reads files newer than the last — cheap, but blind to an edit whose mtime regressed.
- **Reconcile** (`fingerprints` + `load`, used by ``--full``): expose every live
  file's ``(size, mtime)`` so the pipeline can diff against the indexed manifest and
  catch edits the cursor would miss. See the ``TreeSource`` protocol.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fundus.core.ids import content_sha256
from fundus.core.mime import guess_mime
from fundus.models import BlobPayload, Cursor, FileStat, SourceItem


class FilesSource:
    type = "files"

    def __init__(self, name: str, roots: list[str], max_bytes: int | None = None) -> None:
        self.name = name
        self._roots = [Path(r).expanduser() for r in roots]
        self._max_bytes = max_bytes
        self._max_mtime = 0.0

    def _walk(self) -> Iterator[Path]:
        for root in self._roots:
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    yield path

    def _too_big(self, size: int) -> bool:
        return self._max_bytes is not None and size > self._max_bytes

    def _item(self, path: Path, st: os.stat_result) -> SourceItem | None:
        """Build a SourceItem from a file (reading its bytes); None if skipped (oversize/empty)."""
        if self._too_big(st.st_size):
            return None
        data = path.read_bytes()
        if not data:
            return None
        return SourceItem(
            source=self.name,
            type=self.type,
            native_id=str(path),
            item_kind="file",
            title=path.name,
            path=str(path),
            mime_type=guess_mime(data, path.name),
            size=st.st_size,
            mtime=st.st_mtime,
            ts=datetime.fromtimestamp(st.st_mtime, tz=UTC),
            payload=BlobPayload(path=str(path), data=data, sha256=content_sha256(data)),
        )

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        since = float(cursor) if cursor else 0.0
        self._max_mtime = since
        for path in self._walk():
            st = path.stat()
            if st.st_mtime > self._max_mtime:
                self._max_mtime = st.st_mtime
            if st.st_mtime <= since:
                continue
            item = self._item(path, st)
            if item is not None:
                yield item

    def fingerprints(self) -> Iterator[tuple[str, FileStat]]:
        """Every live, indexable file's ``(native_id, FileStat)`` — stat only, no byte reads."""
        for path in self._walk():
            st = path.stat()
            if st.st_mtime > self._max_mtime:
                self._max_mtime = st.st_mtime
            if self._too_big(st.st_size) or st.st_size == 0:
                continue
            yield str(path), FileStat(st.st_size, st.st_mtime)

    def load(self, native_id: str) -> SourceItem | None:
        path = Path(native_id)
        if not path.is_file():
            return None
        return self._item(path, path.stat())

    def live_ids(self) -> Iterator[str]:
        for path in self._walk():
            yield str(path)

    def current_cursor(self) -> Cursor:
        return str(self._max_mtime)
