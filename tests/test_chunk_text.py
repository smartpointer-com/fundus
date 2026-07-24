from datetime import UTC, datetime

from fundus.chunk.base import ChunkParams
from fundus.chunk.text import TextChunker
from fundus.models import Block, EngineRef, ExtractionResult, SourceItem, TextPayload


def _item():
    return SourceItem(
        source="docs",
        type="files",
        native_id="f1",
        item_kind="file",
        ts=datetime(2024, 1, 1, tzinfo=UTC),
        payload=TextPayload(text="x"),
    )


def _doc(blocks):
    return ExtractionResult(engine=EngineRef(name="e", version="1"), blocks=blocks, markdown="")


def test_empty_doc():
    assert TextChunker().chunk(_doc([]), _item()) == []


def test_heading_path_carried():
    blocks = [
        Block(type="heading", level=1, text="Top"),
        Block(type="heading", level=2, text="Sub"),
        Block(type="paragraph", text="Body text here."),
    ]
    chunks = TextChunker().chunk(_doc(blocks), _item())
    assert chunks
    assert chunks[0].heading_path[:2] == ["Top", "Sub"]


def test_table_kept_atomic():
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    blocks = [Block(type="paragraph", text="intro"), Block(type="table", text=table)]
    chunks = TextChunker().chunk(_doc(blocks), _item())
    assert any(table in c.text for c in chunks)


def _markers(text: str) -> set[str]:
    return {w for w in text.split() if w.startswith("P") and w[1:].isdigit()}


def test_multi_chunk_with_overlap():
    params = ChunkParams(target_tokens=120, max_tokens=400, overlap_tokens=90)
    blocks = [Block(type="paragraph", text=f"P{i} " + "word " * 35) for i in range(8)]
    chunks = TextChunker(params).chunk(_doc(blocks), _item())
    assert len(chunks) >= 2
    assert _markers(chunks[0].text) & _markers(chunks[1].text)  # overlap shares a paragraph


def test_oversized_paragraph_split():
    params = ChunkParams(target_tokens=50, max_tokens=80, overlap_tokens=0)
    chunks = TextChunker(params).chunk(_doc([Block(type="paragraph", text="word " * 400)]), _item())
    assert len(chunks) >= 2
