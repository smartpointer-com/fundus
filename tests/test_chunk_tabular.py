from datetime import datetime

from fundus.chunk.base import ChunkParams
from fundus.chunk.tabular import TabularChunker, parse_sheets
from fundus.models import BlobPayload, SourceItem


def _item(data=b""):
    return SourceItem(
        source="docs",
        type="files",
        native_id="sheet1",
        item_kind="file",
        mime_type="text/csv",
        ts=datetime(2024, 1, 1),
        payload=BlobPayload(data=data),
    )


def test_chunk_sheet_repeats_header_and_groups():
    rows = [["name", "value"]] + [[f"r{i}", str(i)] for i in range(20)]
    params = ChunkParams(target_tokens=10, max_tokens=40, overlap_tokens=0)
    chunks = TabularChunker(params).chunk_sheet("Sheet1", rows, _item(), 0)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.text.startswith("name | value")  # header repeated in every chunk
        assert c.heading_path == ["Sheet1"]
        assert c.meta["sheet"] == "Sheet1"
    assert chunks[0].meta["row_start"] == 1


def test_parse_csv():
    sheets = parse_sheets(b"a,b\n1,2\n3,4\n", "text/csv")
    assert sheets == [("", [["a", "b"], ["1", "2"], ["3", "4"]])]


def test_chunk_via_payload_csv():
    data = b"name,value\n" + b"\n".join(f"r{i},{i}".encode() for i in range(20))
    chunks = TabularChunker(ChunkParams(max_tokens=40)).chunk(None, _item(data))
    assert len(chunks) >= 2
    assert all(c.text.startswith("name | value") for c in chunks)
