"""Core domain models shared across the Fundus pipeline.

These are the data contracts that flow between the building blocks: a source
emits ``SourceItem``s; an extractor turns document bytes into an
``ExtractionResult`` (ordered ``Block``s plus a Markdown serialization); a
chunker turns that into ``Chunk``s; and the sink stores ``IndexDocument``s.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# An opaque, per-source incremental position (e.g. a notmuch lastmod, a max
# rowid, or an ISO timestamp). Only the owning source interprets it.
Cursor = str


class TextPayload(BaseModel):
    """Already-textual content (e.g. an email body or a chat window).

    Bypasses the extraction engines entirely.
    """

    kind: Literal["text"] = "text"
    text: str
    content_type: str = "text/plain"  # or "text/markdown"


class BlobPayload(BaseModel):
    """Binary document content that must pass through an extraction engine."""

    kind: Literal["blob"] = "blob"
    path: str | None = None  # local path, if available
    data: bytes | None = None  # or inline bytes
    sha256: str | None = None


Payload = TextPayload | BlobPayload


def payload_bytes(payload: object) -> bytes:
    """Raw bytes for a payload: inline ``data`` if present, else read its ``path`` (else empty)."""
    data = getattr(payload, "data", None)
    if isinstance(data, bytes):
        return data
    path = getattr(payload, "path", None)
    if isinstance(path, str):
        return Path(path).read_bytes()
    return b""


class SourceItem(BaseModel):
    """One logical artifact emitted by a source (an email, chat window, or file)."""

    source: str  # configured source instance name
    type: str  # connector type ("notmuch", "wacli", "files", ...)
    native_id: str  # stable id within the source
    item_kind: Literal["email", "chat_window", "file"]
    title: str | None = None
    path: str | None = None
    mime_type: str | None = None
    ts: datetime
    ts_start: datetime | None = None
    ts_end: datetime | None = None
    actors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    payload: Payload
    extra: dict[str, Any] = Field(default_factory=dict)


class TableData(BaseModel):
    rows: list[list[str]] = Field(default_factory=list)
    header_rows: int = 0
    markdown: str = ""


class Block(BaseModel):
    """One structural element, in reading order."""

    type: Literal[
        "heading",
        "paragraph",
        "list",
        "list_item",
        "table",
        "caption",
        "figure",
        "code",
        "page_break",
        "header",
        "footer",
    ]
    text: str = ""
    level: int | None = None  # heading level
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    table: TableData | None = None


class EngineRef(BaseModel):
    name: str
    version: str


class DocMeta(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    created: datetime | None = None
    modified: datetime | None = None
    languages: list[str] = Field(default_factory=list)
    page_count: int | None = None
    ocr_used: bool = False
    extra: dict[str, Any] = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    """Normalized, engine-agnostic extraction output.

    Rich engines populate ``blocks`` (and tables) faithfully; flat engines
    degrade to a simple paragraph/page stream. Downstream chunking consumes this
    structure, never the engine's native output.
    """

    engine: EngineRef
    blocks: list[Block] = Field(default_factory=list)
    markdown: str = ""
    metadata: DocMeta = Field(default_factory=DocMeta)

    @classmethod
    def from_text(cls, text: str, *, engine: EngineRef | None = None) -> "ExtractionResult":
        """Wrap already-textual content as a single-stream result."""
        eng = engine or EngineRef(name="passthrough", version="1")
        return cls(engine=eng, blocks=[Block(type="paragraph", text=text)], markdown=text)


class Chunk(BaseModel):
    parent_id: str  # the artifact this chunk belongs to
    seq: int
    text: str
    heading_path: list[str] = Field(default_factory=list)  # carried structural context
    page: int | None = None
    token_count: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)  # chunk-type extras (msg_ids, sheet, ts)


class IndexDocument(BaseModel):
    """The record actually stored in the search index."""

    id: str  # deterministic: hash(source, native_id, seq)
    source: str
    item_kind: str
    parent_id: str
    # Raw source handle for follow-up: email Message-ID, file path, chat JID, or "<msgid>#<part>"
    # for an attachment. Lets a search result be acted on (open the file, fetch the mail/message).
    native_id: str = ""
    title: str | None = None
    body: str = ""  # searchable + auto-embedded
    ts: int = 0  # epoch seconds, sortable
    actors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    path: str | None = None
    mime: str | None = None
    lang: list[str] = Field(default_factory=list)
    msg_ids: list[str] | None = None  # e.g. expand a chat window back to messages
    # Fan-out only: a vector Fundus computed for this doc. Not a stored field — the sink lifts it
    # into Meili's `_vectors` on upsert and drops it from the document body.
    vector: list[float] | None = None
