from datetime import UTC, datetime

from fundus.core.ids import doc_id
from fundus.index.mapping import to_index_document
from fundus.models import Chunk, SourceItem, TextPayload


def _item(**kw):
    base = {
        "source": "docs",
        "type": "files",
        "native_id": "f1",
        "item_kind": "file",
        "ts": datetime(2024, 1, 1, tzinfo=UTC),
        "payload": TextPayload(text="x"),
    }
    base.update(kw)
    return SourceItem(**base)


def test_doc_mapping_folds_heading_path():
    item = _item(title="T", tags=["a"], path="/p", mime_type="application/pdf")
    chunk = Chunk(parent_id="par", seq=2, text="body", heading_path=["H1", "H2"])
    doc = to_index_document(item, chunk)
    assert doc.id == doc_id("docs", "f1", 2)
    assert doc.parent_id == "par"
    assert doc.title == "T"
    assert doc.body.startswith("H1 > H2")
    assert doc.ts == int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    assert doc.tags == ["a"]
    assert doc.mime == "application/pdf"


def test_chat_mapping_uses_meta():
    item = _item(source="chat", type="wacli", native_id="jid", item_kind="chat_window")
    chunk = Chunk(
        parent_id="p", seq=0, text="a: hi", meta={"msg_ids": ["1", "2"], "ts": 1700000000, "actors": ["a"]}
    )
    doc = to_index_document(item, chunk)
    assert doc.msg_ids == ["1", "2"]
    assert doc.ts == 1700000000
    assert doc.actors == ["a"]


def test_mapping_carries_extraction_provenance():
    chunk = Chunk(parent_id="p", seq=0, text="x")
    doc = to_index_document(_item(), chunk, extract_sig="docling-serve:ocrmac", ocr_used=True)
    assert doc.extract_sig == "docling-serve:ocrmac"
    assert doc.ocr_used is True
    # defaults stay empty for callers that don't extract (text payloads, tests)
    plain = to_index_document(_item(), chunk)
    assert plain.extract_sig == "" and plain.ocr_used is False
