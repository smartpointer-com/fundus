from datetime import datetime

from fundus.chunk.chat import ChatChunker
from fundus.chunk.dispatch import chunker_for, is_tabular
from fundus.chunk.tabular import TabularChunker
from fundus.chunk.text import TextChunker
from fundus.models import BlobPayload, SourceItem, TextPayload


def _item(kind, mime=None, payload=None):
    return SourceItem(
        source="s",
        type="t",
        native_id="n",
        item_kind=kind,
        mime_type=mime,
        ts=datetime(2024, 1, 1),
        payload=payload or TextPayload(text="x"),
    )


def test_is_tabular():
    assert is_tabular("text/csv")
    assert not is_tabular("application/pdf")
    assert not is_tabular(None)


def test_dispatch_routes_by_kind_and_mime():
    assert isinstance(chunker_for(_item("file", "text/csv", BlobPayload())), TabularChunker)
    assert isinstance(chunker_for(_item("file", "application/pdf")), TextChunker)
    assert isinstance(chunker_for(_item("email")), TextChunker)
    assert isinstance(chunker_for(_item("chat_window")), ChatChunker)
