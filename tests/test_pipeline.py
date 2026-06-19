from datetime import datetime

from fundus.config import FundusConfig
from fundus.core.ids import parent_id
from fundus.core.pipeline import Pipeline, to_extraction
from fundus.extract.base import ExtractOptions
from fundus.models import (
    Block,
    BlobPayload,
    DocMeta,
    EngineRef,
    ExtractionResult,
    SourceItem,
    TextPayload,
)


class FakeSink:
    def __init__(self):
        self.docs = []
        self.deleted = []

    def ensure_schema(self, settings):
        pass

    def upsert(self, docs):
        self.docs.extend(docs)

    def delete_missing(self, source, live):
        self.deleted.append((source, set(live)))
        return len(live)


class FakeExtractor:
    name = "fake"
    version = "1"

    def __init__(self):
        self.calls = 0

    def extract(self, req):
        self.calls += 1
        return ExtractionResult(
            engine=EngineRef(name="fake", version="1"),
            blocks=[Block(type="paragraph", text="EXTRACTED:" + req.data.decode("utf-8", "replace"))],
            markdown="EXTRACTED",
            metadata=DocMeta(),
        )


class DictCache:
    def __init__(self):
        self.d = {}

    def get(self, key):
        return self.d.get(key.as_str())

    def put(self, key, res):
        self.d[key.as_str()] = res


class DictState:
    def __init__(self):
        self.d = {}

    def get_cursor(self, s):
        return self.d.get(s)

    def set_cursor(self, s, c):
        self.d[s] = c


class FakeSource:
    type = "x"

    def __init__(self, name, items, live=None):
        self.name = name
        self._items = items
        self._live = live or []

    def changed(self, cursor):
        return iter(self._items)

    def live_ids(self):
        return iter(self._live)

    def current_cursor(self):
        return "C1"


def _pipeline(sink, extractor):
    return Pipeline(FundusConfig(), sink, extractor, DictCache(), DictState(), batch_size=2)


def _item(**kw):
    base = dict(source="s", type="x", native_id="n", item_kind="file", ts=datetime(2024, 1, 1))
    base.update(kw)
    return SourceItem(**base)


def test_email_text_path_skips_extractor():
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="mail", item_kind="email", title="S", payload=TextPayload(text="email body"))
    n = _pipeline(sink, ex).index_source(FakeSource("mail", [item]))
    assert n >= 1 and ex.calls == 0
    assert any("email body" in d.body for d in sink.docs)


def test_pdf_uses_extractor():
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="docs", native_id="f.pdf", mime_type="application/pdf", title="f.pdf", payload=BlobPayload(data=b"PDFBYTES"))
    _pipeline(sink, ex).index_source(FakeSource("docs", [item]))
    assert ex.calls == 1
    assert any("EXTRACTED" in d.body for d in sink.docs)


def test_text_file_skips_extractor():
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="docs", native_id="a.txt", mime_type="text/plain", payload=BlobPayload(data=b"plain text content"))
    _pipeline(sink, ex).index_source(FakeSource("docs", [item]))
    assert ex.calls == 0
    assert any("plain text content" in d.body for d in sink.docs)


def test_tabular_file_chunked():
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="docs", native_id="s.csv", mime_type="text/csv", payload=BlobPayload(data=b"a,b\n1,2\n"))
    _pipeline(sink, ex).index_source(FakeSource("docs", [item]))
    assert ex.calls == 0
    assert any("a | b" in d.body for d in sink.docs)


def test_chat_indexed_with_meta():
    sink, ex = FakeSink(), FakeExtractor()
    msgs = [{"id": "1", "ts": "2024-01-01T00:00:00", "sender": "a", "text": "hi"}]
    item = _item(source="chat", native_id="jid", item_kind="chat_window", payload=TextPayload(text=""), extra={"messages": msgs})
    _pipeline(sink, ex).index_source(FakeSource("chat", [item]))
    assert any(d.msg_ids == ["1"] for d in sink.docs)


def test_cursor_advances():
    sink, ex = FakeSink(), FakeExtractor()
    p = _pipeline(sink, ex)
    p.index_source(FakeSource("mail", [_item(source="mail", item_kind="email", payload=TextPayload(text="x"))]))
    assert p._state.get_cursor("mail") == "C1"


def test_full_reconcile_uses_live_parents():
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="mail", native_id="m1", item_kind="email", payload=TextPayload(text="x"))
    _pipeline(sink, ex).index_source(FakeSource("mail", [item], live=["m1"]), full=True)
    assert sink.deleted and sink.deleted[0][0] == "mail"
    assert parent_id("mail", "m1") in sink.deleted[0][1]


def test_to_extraction_html_strips_tags():
    res = to_extraction(
        _item(mime_type="text/html", payload=BlobPayload(data=b"<p>Hi <b>x</b></p>")),
        FakeExtractor(),
        DictCache(),
        ExtractOptions(),
    )
    assert "x" in res.markdown
