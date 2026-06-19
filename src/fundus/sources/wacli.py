"""WhatsApp/chat connector reading a wacli SQLite database (read-only).

The schema is fully configurable (table and column names) so it works against any
similar message store; defaults match the common wacli ``messages`` layout. When a
chat has new messages, the whole chat is re-emitted as one ``chat_window``
SourceItem carrying its messages in ``extra["messages"]`` for the chat chunker to
window. The cursor is the maximum message timestamp seen.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from fundus.models import Cursor, SourceItem, TextPayload

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
        self._connect = connect or self._ro_connect
        self._next_cursor = "0"

    def _ro_connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self._db}?mode=ro&immutable=1", uri=True)

    def _text_expr(self) -> str:
        return "COALESCE(" + ", ".join([*self._text_cols, "''"]) + ")"

    def _and_where(self) -> str:
        return f" AND ({self._where})" if self._where else ""

    def changed(self, cursor: Cursor | None) -> Iterator[SourceItem]:
        since = int(cursor) if cursor else 0
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
        finally:
            conn.close()

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
        conn = self._connect()
        try:
            for r in conn.execute(f"SELECT DISTINCT {self._chat} FROM {self._t}"):
                yield str(r[0])
        finally:
            conn.close()

    def current_cursor(self) -> Cursor:
        return self._next_cursor
