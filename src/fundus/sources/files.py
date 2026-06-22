"""Filesystem connector: walk configured roots, emit changed files by mtime.

Each file becomes a ``file`` SourceItem carrying its bytes as a ``BlobPayload``;
the pipeline decides whether to extract (documents) or chunk directly (tabular).
The cursor is the maximum mtime seen, stored as an epoch string.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from fundus.core.ids import content_sha256
from fundus.core.mime import guess_mime
from fundus.models import BlobPayload, Cursor, SourceItem


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

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        since = float(cursor) if cursor else 0.0
        self._max_mtime = since
        for path in self._walk():
            mtime = path.stat().st_mtime
            if mtime > self._max_mtime:
                self._max_mtime = mtime
            if mtime <= since:
                continue
            if self._max_bytes is not None and path.stat().st_size > self._max_bytes:
                continue
            data = path.read_bytes()
            if not data:
                continue
            yield SourceItem(
                source=self.name,
                type=self.type,
                native_id=str(path),
                item_kind="file",
                title=path.name,
                path=str(path),
                mime_type=guess_mime(data, path.name),
                ts=datetime.fromtimestamp(mtime, tz=UTC),
                payload=BlobPayload(path=str(path), data=data, sha256=content_sha256(data)),
            )

    def live_ids(self) -> Iterator[str]:
        for path in self._walk():
            yield str(path)

    def current_cursor(self) -> Cursor:
        return str(self._max_mtime)
