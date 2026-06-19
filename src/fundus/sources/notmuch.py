"""notmuch email connector.

Shells out to the ``notmuch`` CLI (no bindings): ``count --lastmod`` for the
revision cursor, ``show --format=json`` for message bodies/headers/tags, and
``search --output=messages`` for live ids. Incremental via notmuch's
``lastmod:N..`` revision range.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from fundus.models import Cursor, SourceItem, TextPayload

Runner = Callable[[list[str]], str]


def _iter_messages(node: Any) -> Iterator[dict[str, Any]]:
    """Walk notmuch's nested thread/message JSON, yielding message dicts."""
    if isinstance(node, dict):
        if "id" in node:
            yield node
    elif isinstance(node, list):
        for item in node:
            yield from _iter_messages(item)


def _collect(parts: list[dict[str, Any]], plain: list[str], html: list[str]) -> None:
    for part in parts:
        ctype = str(part.get("content-type", "")).lower()
        content = part.get("content")
        if ctype.startswith("multipart") and isinstance(content, list):
            _collect(content, plain, html)
        elif ctype == "text/plain" and isinstance(content, str):
            plain.append(content)
        elif ctype == "text/html" and isinstance(content, str):
            html.append(content)


def message_body(message: dict[str, Any]) -> str:
    plain: list[str] = []
    html: list[str] = []
    _collect(message.get("body", []) or [], plain, html)
    if plain:
        return "\n".join(plain).strip()
    if html:
        from selectolax.parser import HTMLParser

        return "\n".join(HTMLParser(h).text() for h in html).strip()
    return ""


class NotmuchSource:
    type = "notmuch"

    def __init__(
        self,
        name: str,
        db: str | None = None,
        query: str = "*",
        notmuch_bin: str = "notmuch",
        runner: Runner | None = None,
    ) -> None:
        self.name = name
        self._db = db
        self._query = query
        self._bin = notmuch_bin
        self._run = runner or self._default_run
        self._next_cursor = "0"

    def _default_run(self, args: list[str]) -> str:
        env = os.environ.copy()
        if self._db:
            env["NOTMUCH_DATABASE"] = self._db
        return subprocess.run(
            [self._bin, *args], capture_output=True, text=True, env=env, check=True
        ).stdout

    def _lastmod(self) -> str:
        out = self._run(["count", "--lastmod", self._query]).split()
        return out[-1] if out else "0"

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        self._next_cursor = self._lastmod()
        query = f"({self._query}) and lastmod:{cursor or '0'}.."
        out = self._run(["show", "--format=json", "--format-version=5", "--body=true", query])
        for message in _iter_messages(json.loads(out) if out.strip() else []):
            headers = message.get("headers", {})
            timestamp = int(message.get("timestamp", 0))
            actors = [headers[k] for k in ("From", "To", "Cc") if headers.get(k)]
            yield SourceItem(
                source=self.name,
                type=self.type,
                native_id=str(message["id"]),
                item_kind="email",
                title=headers.get("Subject"),
                ts=datetime.fromtimestamp(timestamp, tz=UTC),
                actors=actors,
                tags=list(message.get("tags", [])),
                payload=TextPayload(text=message_body(message)),
            )

    def live_ids(self) -> Iterator[str]:
        out = self._run(["search", "--output=messages", self._query])
        for line in out.splitlines():
            line = line.strip()
            if line:
                yield line[3:] if line.startswith("id:") else line

    def current_cursor(self) -> Cursor:
        return self._next_cursor
