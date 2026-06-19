"""Select the chunker for a SourceItem by kind and MIME type."""

from __future__ import annotations

from fundus.chunk.base import Chunker
from fundus.chunk.chat import ChatChunker
from fundus.chunk.tabular import TabularChunker
from fundus.chunk.text import TextChunker
from fundus.models import SourceItem

_TABULAR_MIME = {
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.oasis.opendocument.spreadsheet",
}


def is_tabular(mime: str | None) -> bool:
    return bool(mime) and mime in _TABULAR_MIME


def chunker_for(item: SourceItem) -> Chunker:
    if item.item_kind == "chat_window":
        return ChatChunker()
    if item.item_kind == "file" and is_tabular(item.mime_type):
        return TabularChunker()
    return TextChunker()
