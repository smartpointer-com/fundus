"""Conversation-window chunker for chat messages.

A chat ``SourceItem`` carries its messages in ``item.extra["messages"]``; this
chunker windows consecutive messages into bounded, overlapping chunks. A long
silence is a soft cut hint (only past the target), never a hard trigger — chats
span days with multi-hour gaps. Each chunk records its message ids, participants,
and time span in ``Chunk.meta`` so a hit expands back to the exact messages.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from fundus.chunk.tokens import count_tokens
from fundus.core.ids import parent_id
from fundus.models import Chunk, ExtractionResult, SourceItem


class ChatMessage(BaseModel):
    id: str
    ts: datetime
    sender: str = ""
    text: str = ""


class ChatWindowParams(BaseModel):
    target_tokens: int = 512
    max_tokens: int = 1024
    overlap_messages: int = 2
    soft_gap_hours: float = 24.0


def render_line(message: ChatMessage) -> str:
    return f"{message.sender}: {message.text}" if message.sender else message.text


def transcript(messages: list[ChatMessage]) -> str:
    return "\n".join(render_line(m) for m in messages)


def window_messages(
    messages: list[ChatMessage], params: ChatWindowParams | None = None
) -> list[list[ChatMessage]]:
    p = params or ChatWindowParams()
    ordered = sorted(messages, key=lambda m: m.ts)
    gap = timedelta(hours=p.soft_gap_hours)
    windows: list[list[ChatMessage]] = []
    cur: list[ChatMessage] = []
    cur_tokens = 0
    pending_new = False

    def flush() -> None:
        nonlocal cur, cur_tokens, pending_new
        if pending_new and cur:
            windows.append(list(cur))
        keep = cur[-p.overlap_messages :] if p.overlap_messages > 0 else []
        cur = list(keep)
        cur_tokens = sum(count_tokens(render_line(m)) for m in cur)
        pending_new = False

    for message in ordered:
        mtok = count_tokens(render_line(message))
        if cur:
            over_budget = cur_tokens + mtok > p.max_tokens
            long_gap = (message.ts - cur[-1].ts) > gap and cur_tokens >= p.target_tokens
            if over_budget or long_gap:
                flush()
        cur.append(message)
        cur_tokens += mtok
        pending_new = True
    if pending_new:
        flush()
    return windows


class ChatChunker:
    def __init__(self, params: ChatWindowParams | None = None) -> None:
        self.p = params or ChatWindowParams()

    def chunk_messages(self, messages: list[ChatMessage], item: SourceItem) -> list[Chunk]:
        pid = parent_id(item.source, item.native_id)
        chunks: list[Chunk] = []
        for seq, win in enumerate(window_messages(messages, self.p)):
            text = transcript(win)
            chunks.append(
                Chunk(
                    parent_id=pid,
                    seq=seq,
                    text=text,
                    meta={
                        "msg_ids": [m.id for m in win],
                        "ts": int(win[-1].ts.timestamp()),
                        "actors": sorted({m.sender for m in win if m.sender}),
                    },
                )
            )
        return chunks

    def chunk(self, doc: ExtractionResult, item: SourceItem) -> list[Chunk]:
        raw = item.extra.get("messages", [])
        messages = [m if isinstance(m, ChatMessage) else ChatMessage(**m) for m in raw]
        return self.chunk_messages(messages, item)
