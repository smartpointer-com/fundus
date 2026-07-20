"""notmuch email connector.

Shells out to the ``notmuch`` CLI (no bindings): ``count --lastmod`` for the
revision cursor and ``show --format=json`` for message bodies/headers/tags. Live
ids (for a ``--full`` reconcile) come from ``search --output=messages`` when
attachments are disabled; with attachments on (the default) ``show --body=true``
enumerates message ids plus attachment part ids, and attachment bytes are read
with ``show --format=raw --part=N``. Incremental via notmuch's ``lastmod:N..``
revision range.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import structlog

from fundus.models import BlobPayload, Cursor, SourceItem, TextPayload

log = structlog.get_logger("fundus.sources.notmuch")

Runner = Callable[[list[str]], str]
# (message_id, part_id) -> raw decoded bytes of that MIME part.
PartRunner = Callable[[str, int], bytes]

# Attachment MIME types worth extracting (documents). Images and inline body parts are skipped:
# they are mostly signatures/logos/photos (noise plus OCR cost) and can be enabled later.
_ATTACHMENT_TYPES = frozenset({
    "application/pdf",
    "application/rtf",
    "text/rtf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "text/csv",
    "text/tab-separated-values",
})


def _attachment_parts(parts: list[dict[str, Any]]) -> Iterator[tuple[int, str, str]]:
    """Yield ``(part_id, filename, mime)`` for document-type attachment parts.

    Descends into multipart containers (whose ``content`` is a list of child parts).
    Only document MIME types are returned; images and text body parts are skipped.
    """
    for part in parts:
        content = part.get("content")
        if isinstance(content, list):
            yield from _attachment_parts(content)
            continue
        mime = str(part.get("content-type", "")).split(";")[0].strip().lower()
        if mime in _ATTACHMENT_TYPES and part.get("id") is not None:
            filename = str(part.get("filename") or f"part-{part['id']}")
            yield int(part["id"]), filename, mime


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
        *,
        attachments: bool = True,
        part_runner: PartRunner | None = None,
    ) -> None:
        self.name = name
        self._db = db
        self._query = query
        self._bin = notmuch_bin
        self._run = runner or self._default_run
        self._part_run = part_runner or self._default_part_run
        self._attachments = attachments
        self._next_cursor = "0"

    def _default_run(self, args: list[str]) -> str:
        env = os.environ.copy()
        if self._db:
            env["NOTMUCH_DATABASE"] = self._db
        # Capture bytes and decode leniently. notmuch's JSON can carry non-UTF-8 bytes from raw
        # message parts; a strict whole-dump decode (text=True) raises and aborts the entire
        # index on a single bad byte. errors="replace" keeps the JSON well-formed (the bad byte
        # becomes U+FFFD inside a string value) so one malformed mail can't sink the run.
        completed = subprocess.run([self._bin, *args], capture_output=True, env=env, check=True)
        return completed.stdout.decode("utf-8", errors="replace")

    def _default_part_run(self, message_id: str, part_id: int) -> bytes:
        env = os.environ.copy()
        if self._db:
            env["NOTMUCH_DATABASE"] = self._db
        # message_id is the bare Message-ID; notmuch needs it as an `id:` query term.
        return subprocess.run(
            [self._bin, "show", "--format=raw", f"--part={part_id}", f"id:{message_id}"],
            capture_output=True,
            env=env,
            check=True,
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
            ts = datetime.fromtimestamp(int(message.get("timestamp", 0)), tz=UTC)
            actors = [headers[k] for k in ("From", "To", "Cc") if headers.get(k)]
            tags = list(message.get("tags", []))
            message_id = str(message["id"])
            yield SourceItem(
                source=self.name,
                type=self.type,
                native_id=message_id,
                item_kind="email",
                title=headers.get("Subject"),
                ts=ts,
                actors=actors,
                tags=tags,
                payload=TextPayload(text=message_body(message)),
            )
            if self._attachments:
                yield from self._attachment_items(message_id, message, ts, actors, tags)

    def _attachment_items(
        self, message_id: str, message: dict[str, Any], ts: datetime,
        actors: list[str], tags: list[str],
    ) -> Iterator[SourceItem]:
        """Emit one item per document attachment, fetching its raw bytes on demand.

        Each becomes its own document (``<msgid>#<part>``) routed through the binary
        extraction pipeline, inheriting the email's timestamp/actors/tags. A part whose
        bytes can't be fetched is skipped — it must not abort the email or the run.
        """
        for part_id, filename, mime in _attachment_parts(message.get("body") or []):
            try:
                data = self._part_run(message_id, part_id)
            except Exception as exc:  # noqa: BLE001 - a bad part must not sink the email
                log.warning("attachment fetch failed", message_id=message_id,
                            part=part_id, filename=filename, error=str(exc))
                continue
            yield SourceItem(
                source=self.name,
                type=self.type,
                native_id=f"{message_id}#{part_id}",
                item_kind="file",  # an attachment is a document: chunk it like any other file
                title=filename,
                ts=ts,
                actors=actors,
                tags=[*tags, "attachment"],
                mime_type=mime,
                payload=BlobPayload(data=data),
            )

    def live_ids(self) -> Iterator[str]:
        """Every live native_id: message ids, plus attachment part ids (``<msgid>#<part>``) when
        attachments are indexed — so a ``--full`` reconcile keeps live attachments and prunes only
        those of deleted mail. Enumerating parts needs a full ``show`` (notmuch emits part structure
        only with ``--body=true``); the cheap message-id search suffices when attachments are off.
        """
        if not self._attachments:
            out = self._run(["search", "--output=messages", self._query])
            for line in out.splitlines():
                line = line.strip()
                if line:
                    yield line[3:] if line.startswith("id:") else line
            return
        out = self._run(["show", "--format=json", "--format-version=5", "--body=true", self._query])
        for message in _iter_messages(json.loads(out) if out.strip() else []):
            mid = str(message["id"])
            yield mid
            for part_id, _filename, _mime in _attachment_parts(message.get("body") or []):
                yield f"{mid}#{part_id}"

    def locate(self, message_id: str) -> list[str]:
        """Maildir file path(s) for a Message-ID, so a consumer can open the original email."""
        out = self._run(["search", "--output=files", f"id:{message_id}"])
        return [line.strip() for line in out.splitlines() if line.strip()]

    def current_cursor(self) -> Cursor:
        return self._next_cursor
