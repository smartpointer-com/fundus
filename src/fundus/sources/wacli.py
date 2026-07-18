"""WhatsApp/chat connector reading a wacli SQLite database (read-only).

The schema is fully configurable (table and column names) so it works against any
similar message store; defaults match the common wacli ``messages`` layout. When a
chat has new messages, the whole chat is re-emitted as one ``chat_window``
SourceItem carrying its messages in ``extra["messages"]`` for the chat chunker to
window. The cursor is the maximum message timestamp seen.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fundus.models import BlobPayload, Cursor, SourceItem, TextPayload

Connect = Callable[[], sqlite3.Connection]


def to_datetime(value: Any) -> datetime:
    """Coerce an epoch (s/ms/us) or ISO string to an aware datetime."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            value = float(value)
    n = float(value)
    if n > 1e14:
        n /= 1e6  # microseconds
    elif n > 1e11:
        n /= 1e3  # milliseconds
    return datetime.fromtimestamp(n, tz=UTC)


class WacliSource:
    type = "wacli"

    def __init__(
        self,
        name: str,
        db: str,
        *,
        table: str = "messages",
        id_col: str = "msg_id",
        chat_col: str = "chat_jid",
        chat_name_col: str = "chat_name",
        ts_col: str = "ts",
        sender_col: str = "sender_name",
        text_cols: tuple[str, ...] = ("text", "media_caption", "display_text"),
        where: str | None = None,
        media: bool = True,
        media_type_col: str = "media_type",
        filename_col: str = "filename",
        mime_col: str = "mime_type",
        path_col: str = "local_path",
        document_media_type: str = "document",
        media_dir: str | None = None,
        tags: tuple[str, ...] = ("whatsapp", "attachment"),
        connect: Connect | None = None,
    ) -> None:
        self.name = name
        self._db = str(db)
        self._t = table
        self._id = id_col
        self._chat = chat_col
        self._chat_name = chat_name_col
        self._ts = ts_col
        self._sender = sender_col
        self._text_cols = list(text_cols)
        self._where = where
        self._media = media
        self._media_type_col = media_type_col
        self._filename_col = filename_col
        self._mime_col = mime_col
        self._path_col = path_col
        self._document_media_type = document_media_type
        # Stamped on emitted media items. Configurable because this connector reads any
        # wacli-shaped store, not only WhatsApp — a Slack archive's PDFs are not "whatsapp".
        self._tags = list(tags)
        # Stored local_path values can be stale (the store may have moved); keep only the part after
        # ".../media/" and rejoin it to the current media dir (default: alongside the db).
        self._media_dir = media_dir or str(Path(self._db).parent / "media")
        self._connect = connect or self._ro_connect
        self._next_cursor = "0"

    def _ro_connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._db}?mode=ro&immutable=1", uri=True)

    def _text_expr(self) -> str:
        return "COALESCE(" + ", ".join([*self._text_cols, "''"]) + ")"

    def _and_where(self) -> str:
        return f" AND ({self._where})" if self._where else ""

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        # Float, not int: `ts` is whatever the backing store uses. wacli's own is INTEGER
        # seconds, but a wacli-shaped view over another store (a Slack archive's float
        # ts_epoch) carries sub-second precision, and int() would reject its own cursor.
        since = float(cursor) if cursor else 0.0
        conn = self._connect()
        try:
            row = conn.execute(f"SELECT MAX({self._ts}) FROM {self._t}").fetchone()
            self._next_cursor = str(row[0] if row and row[0] is not None else 0)
            chats = [
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT {self._chat} FROM {self._t} "
                    f"WHERE {self._ts} > ?{self._and_where()}",
                    (since,),
                )
            ]
            for chat in chats:
                yield self._chat_item(conn, chat)
            if self._media:
                yield from self._media_items(conn, since)
        finally:
            conn.close()

    def _media_items(self, conn: sqlite3.Connection, since: float) -> Iterator[SourceItem]:
        """Emit a file item per shared *document* (PDF/spreadsheet/etc.), analogous to an email
        attachment — routed through the binary-extraction pipeline. Images/video/audio are skipped
        (media_type filter), and only messages whose media was downloaded locally are emitted.

        These per-message file ids are also enumerated by live_ids, so a --full reconcile keeps
        live shared documents and prunes those whose message or local file is gone.
        """
        rows = conn.execute(
            f"SELECT {self._id}, {self._ts}, {self._sender}, {self._filename_col}, "
            f"{self._mime_col}, {self._path_col} FROM {self._t} "
            f"WHERE {self._media_type_col} = ? AND {self._path_col} IS NOT NULL "
            f"AND {self._ts} > ?{self._and_where()}",
            (self._document_media_type, since),
        ).fetchall()
        for msg_id, ts, sender, filename, mime, local_path in rows:
            path = self._resolve_media_path(str(local_path))
            if path is None:
                continue  # not present locally (stale path / not downloaded) -> can't index
            yield SourceItem(
                source=self.name,
                type=self.type,
                native_id=str(msg_id),
                item_kind="file",
                title=str(filename) if filename else "document",
                ts=to_datetime(ts),
                actors=[sender] if sender else [],
                tags=list(self._tags),
                mime_type=mime,
                payload=BlobPayload(path=path),
            )

    def _resolve_media_path(self, local_path: str) -> str | None:
        rest = local_path.split("/media/", 1)
        path = os.path.join(self._media_dir, rest[1]) if len(rest) == 2 else local_path
        return path if os.path.exists(path) else None

    def _chat_item(self, conn: sqlite3.Connection, chat: Any) -> SourceItem:
        rows = conn.execute(
            f"SELECT {self._id}, {self._ts}, {self._sender}, {self._text_expr()}, {self._chat_name} "
            f"FROM {self._t} WHERE {self._chat} = ?{self._and_where()} ORDER BY {self._ts}",
            (chat,),
        ).fetchall()
        messages = [
            {"id": str(r[0]), "ts": to_datetime(r[1]).isoformat(), "sender": r[2] or "", "text": r[3]}
            for r in rows
            if (r[3] or "").strip()
        ]
        title = next((r[4] for r in reversed(rows) if r[4]), str(chat))
        return SourceItem(
            source=self.name,
            type=self.type,
            native_id=str(chat),
            item_kind="chat_window",
            title=title,
            ts=to_datetime(rows[-1][1]),
            ts_start=to_datetime(rows[0][1]),
            ts_end=to_datetime(rows[-1][1]),
            actors=sorted({m["sender"] for m in messages if m["sender"]}),
            payload=TextPayload(text=""),
            extra={"messages": messages, "chat": str(chat)},
        )

    def live_ids(self) -> Iterator[str]:
        """Chat jids plus the msg_ids of shared documents present locally (matching what
        ``_media_items`` indexes), so a ``--full`` reconcile doesn't prune live media."""
        conn = self._connect()
        try:
            for r in conn.execute(f"SELECT DISTINCT {self._chat} FROM {self._t}"):
                yield str(r[0])
            if self._media:
                rows = conn.execute(
                    f"SELECT {self._id}, {self._path_col} FROM {self._t} "
                    f"WHERE {self._media_type_col} = ? AND {self._path_col} IS NOT NULL"
                    f"{self._and_where()}",
                    (self._document_media_type,),
                )
                for msg_id, local_path in rows:
                    if self._resolve_media_path(str(local_path)) is not None:
                        yield str(msg_id)
        finally:
            conn.close()

    def current_cursor(self) -> Cursor:
        return self._next_cursor
