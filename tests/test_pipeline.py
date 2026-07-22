from datetime import datetime

import httpx
import pytest

from fundus.config import FundusConfig, SourceConfig
from fundus.core.ids import parent_id
from fundus.core.pipeline import Pipeline, to_extraction
from fundus.extract.base import ExtractOptions
from fundus.models import (
    Block,
    BlobPayload,
    DocMeta,
    EngineRef,
    ExtractionResult,
    FileStat,
    ManifestEntry,
    SourceItem,
    TextPayload,
)


class FakeSink:
    def __init__(self, manifest=None):
        self.docs = []
        self.deleted = []
        self.deleted_parents = []
        # Accept plain FileStat values for convenience; normalize to ManifestEntry.
        self._manifest = {
            nid: v if isinstance(v, ManifestEntry) else ManifestEntry(v)
            for nid, v in (manifest or {}).items()
        }

    def ensure_schema(self, settings):
        pass

    def upsert(self, docs):
        self.docs.extend(docs)

    def delete_missing(self, source, live):
        self.deleted.append((source, set(live)))
        return len(live)

    def delete_parents(self, parent_ids):
        self.deleted_parents.append(set(parent_ids))
        return len(parent_ids)

    def indexed_manifest(self, source):
        return dict(self._manifest)


class FakeExtractor:
    name = "fake"
    version = "1"
    fingerprint = ""

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


def test_index_source_parallel_indexes_all_and_survives_failures():
    sink = FakeSink()

    class FlakyExtractor(FakeExtractor):
        def extract(self, req):
            if req.data == b"BAD":
                raise RuntimeError("boom")
            return super().extract(req)

    items = [
        _item(
            source="docs",
            native_id=f"f{i}.pdf",
            mime_type="application/pdf",
            title="x",
            payload=BlobPayload(data=(b"BAD" if i == 2 else f"doc{i}".encode())),
        )
        for i in range(6)
    ]
    pipeline = Pipeline(
        FundusConfig(), sink, FlakyExtractor(), DictCache(), DictState(),
        batch_size=2, workers=4, retry_backoff=0.0,
    )
    n = pipeline.index_source(FakeSource("docs", items))
    assert n == 5  # the failing item is skipped, the other 5 indexed
    assert len(sink.docs) == 5


def test_process_item_retries_transient_transport_failure():
    class FlakyOnce(FakeExtractor):
        def __init__(self):
            super().__init__()
            self.remaining_failures = 1

        def extract(self, req):
            if self.remaining_failures > 0:
                self.remaining_failures -= 1
                raise httpx.ConnectError("server disconnected")  # transient transport error
            return super().extract(req)

    sink = FakeSink()
    item = _item(
        source="docs", native_id="f.pdf", mime_type="application/pdf",
        title="f.pdf", payload=BlobPayload(data=b"PDFBYTES"),
    )
    pipeline = Pipeline(
        FundusConfig(), sink, FlakyOnce(), DictCache(), DictState(),
        batch_size=2, max_attempts=2, retry_backoff=0.0,
    )
    n = pipeline.index_source(FakeSource("docs", [item]))
    assert n == 1 and len(sink.docs) == 1  # retried past the transient failure


def test_pipeline_fanout_embeds_documents():
    class FakeEmbedder:
        def __init__(self):
            self.batches = []

        def embed(self, texts):
            self.batches.append(texts)
            return [[float(i), 0.0] for i in range(len(texts))]

    emb = FakeEmbedder()
    sink = FakeSink()
    item = _item(source="mail", item_kind="email", title="Subj", payload=TextPayload(text="a body"))
    pipeline = Pipeline(
        FundusConfig(), sink, FakeExtractor(), DictCache(), DictState(),
        batch_size=10, doc_embedder=emb,
    )
    pipeline.index_source(FakeSource("mail", [item]))
    assert sink.docs and all(d.vector is not None for d in sink.docs)
    assert emb.batches and "Subj" in emb.batches[0][0] and "a body" in emb.batches[0][0]


def test_pipeline_embed_cache_hit_skips_embedder(tmp_path):
    from fundus.embed.cache import SqliteEmbeddingCache

    class CountingEmbedder:
        def __init__(self):
            self.calls = 0

        def embed(self, texts):
            self.calls += len(texts)
            return [[0.5, 0.6] for _ in texts]

    emb = CountingEmbedder()
    cache = SqliteEmbeddingCache(str(tmp_path / "e.db"), "m")
    item = _item(source="mail", item_kind="email", title="S", payload=TextPayload(text="hello body"))
    p1 = Pipeline(FundusConfig(), FakeSink(), FakeExtractor(), DictCache(), DictState(),
                  batch_size=10, doc_embedder=emb, embed_cache=cache)
    p1.index_source(FakeSource("mail", [item]))
    first = emb.calls
    assert first >= 1
    # re-running the same content hits the cache -> no further embedder calls
    sink2 = FakeSink()
    p2 = Pipeline(FundusConfig(), sink2, FakeExtractor(), DictCache(), DictState(),
                  batch_size=10, doc_embedder=emb, embed_cache=cache)
    p2.index_source(FakeSource("mail", [item]))
    assert emb.calls == first  # served from cache
    assert sink2.docs and all(
        d.vector == pytest.approx([0.5, 0.6], abs=1e-6) for d in sink2.docs
    )


def test_pipeline_without_embedder_leaves_vectors_unset():
    sink = FakeSink()
    item = _item(source="mail", item_kind="email", payload=TextPayload(text="x"))
    _pipeline(sink, FakeExtractor()).index_source(FakeSource("mail", [item]))
    assert sink.docs and all(d.vector is None for d in sink.docs)


def test_process_item_does_not_retry_permanent_failure():
    class AlwaysHttpError(FakeExtractor):
        def extract(self, req):
            self.calls += 1
            raise httpx.HTTPStatusError(
                "504", request=httpx.Request("POST", "http://x"), response=httpx.Response(504)
            )

    ex = AlwaysHttpError()
    item = _item(
        source="docs", native_id="f.pdf", mime_type="application/pdf",
        title="f.pdf", payload=BlobPayload(data=b"PDFBYTES"),
    )
    pipeline = Pipeline(
        FundusConfig(), FakeSink(), ex, DictCache(), DictState(),
        batch_size=2, max_attempts=3, retry_backoff=0.0,
    )
    n = pipeline.index_source(FakeSource("docs", [item]))
    assert n == 0 and ex.calls == 1  # definitive 504: tried once, not retried


class FakeTreeSource:
    """A file-tree source whose contents are held in memory (satisfies TreeSource)."""

    type = "files"

    def __init__(self, name, files, mime="text/plain"):
        self.name = name  # files: native_id -> (FileStat, bytes)
        self._files = files
        self._mime = mime  # application/pdf routes loads through the extraction engine

    def changed(self, cursor):
        return iter([])

    def live_ids(self):
        return iter(self._files)

    def current_cursor(self):
        return "C1"

    def fingerprints(self):
        for nid, (stat, _data) in self._files.items():
            yield nid, stat

    def load(self, native_id):
        if native_id not in self._files:  # mirrors FilesSource: a vanished file loads as None
            return None
        stat, data = self._files[native_id]
        return SourceItem(
            source=self.name, type="files", native_id=native_id, item_kind="file",
            title=native_id, path=native_id, mime_type=self._mime,
            size=stat.size, mtime=stat.mtime, ts=datetime(2024, 1, 1),
            payload=BlobPayload(data=data),
        )


def test_full_tree_reconcile_reindexes_changed_and_drops_removed():
    # Indexed: a + b. On disk: a edited (size/mtime differ), c new, b gone.
    indexed = {"a": FileStat(2, 100.0), "b": FileStat(2, 100.0)}
    disk = {"a": (FileStat(3, 200.0), b"aaa"), "c": (FileStat(2, 100.0), b"cc")}
    sink = FakeSink(manifest=indexed)
    src = FakeTreeSource("docs", disk)
    n = Pipeline(FundusConfig(), sink, FakeExtractor(), DictCache(), DictState(), batch_size=10).index_source(
        src, full=True
    )
    reindexed = {d.native_id for d in sink.docs}
    assert reindexed == {"a", "c"}  # edited + new re-indexed; unchanged 'b' not touched
    assert n == len(sink.docs)
    # Parents cleared = changed (a, c) ∪ removed (b), so stale chunks (incl. re-chunk orphans) go.
    assert sink.deleted_parents and sink.deleted_parents[0] == {
        parent_id("docs", nid) for nid in ("a", "b", "c")
    }
    assert sink.deleted == []  # tree path uses delete_parents, not the set-difference path


def test_full_tree_reconcile_skips_unchanged():
    indexed = {"a": FileStat(2, 100.0)}
    disk = {"a": (FileStat(2, 100.0), b"aa")}  # identical fingerprint
    sink = FakeSink(manifest=indexed)
    n = Pipeline(FundusConfig(), sink, FakeExtractor(), DictCache(), DictState()).index_source(
        FakeTreeSource("docs", disk), full=True
    )
    assert n == 0 and sink.docs == []  # nothing re-read or re-indexed
    assert sink.deleted_parents == [set()]  # no parents to clear


def test_full_tree_reconcile_refuses_mass_prune():
    # A large indexed manifest vs. a tree that suddenly looks near-empty (unmounted volume,
    # permission failure, resource exhaustion): abort loudly, never prune.
    indexed = {f"f{i}": FileStat(2, 100.0) for i in range(1000)}
    disk = {f"f{i}": (FileStat(2, 100.0), b"aa") for i in range(400)}  # 600 "removed" > 25%
    sink = FakeSink(manifest=indexed)
    pipeline = Pipeline(FundusConfig(), sink, FakeExtractor(), DictCache(), DictState())
    with pytest.raises(RuntimeError, match="refusing to prune"):
        pipeline.index_source(FakeTreeSource("docs", disk), full=True)
    assert sink.deleted_parents == []  # nothing was deleted
    # An intentional purge goes through with the explicit override.
    pipeline.index_source(FakeTreeSource("docs", disk), full=True, allow_mass_delete=True)
    assert len(sink.deleted_parents[0]) == 600


def test_full_tree_reconcile_allows_bounded_prune():
    # Under both bounds (25% and the absolute floor): a genuine deletion goes through.
    indexed = {f"f{i}": FileStat(2, 100.0) for i in range(1000)}
    disk = {f"f{i}": (FileStat(2, 100.0), b"aa") for i in range(800)}  # 200 removed, 20%
    sink = FakeSink(manifest=indexed)
    Pipeline(FundusConfig(), sink, FakeExtractor(), DictCache(), DictState()).index_source(
        FakeTreeSource("docs", disk), full=True
    )
    assert len(sink.deleted_parents[0]) == 200


def test_index_contains_a_failing_source_and_still_runs_the_rest(monkeypatch):
    # A source that blows up before yielding its first item (e.g. an unparseable cursor)
    # must not freeze the cursors of every source configured after it.
    cfg = FundusConfig(sources=[
        SourceConfig(name="mail", type="x"),
        SourceConfig(name="slack", type="x"),
        SourceConfig(name="documents", type="x"),
    ])

    class Exploding(FakeSource):
        def changed(self, cursor):
            raise ValueError("invalid literal for int() with base 10: '1700000000.123456'")

    def fake_build(source_cfg):
        if source_cfg.name == "slack":
            return Exploding("slack", [])
        return FakeSource(source_cfg.name, [_item(source=source_cfg.name, payload=TextPayload(text="hi"))])

    monkeypatch.setattr("fundus.core.pipeline.build_source", fake_build)
    state = DictState()
    pipeline = Pipeline(cfg, FakeSink(), FakeExtractor(), DictCache(), state, batch_size=2)
    report = pipeline.index()

    assert set(report.counts) == {"mail", "documents"}  # both sides of the failure ran
    assert list(report.failures) == ["slack"]
    assert "1700000000.123456" in report.failures["slack"]
    assert state.get_cursor("documents") == "C1"  # the source *after* the failure advanced
    assert state.get_cursor("slack") is None  # the broken one did not


def test_docs_carry_extract_sig_and_ocr_used():
    class OcrExtractor(FakeExtractor):
        name = "docling-serve"
        fingerprint = "ocrmac"

        def extract(self, req):
            self.calls += 1
            return ExtractionResult(
                engine=EngineRef(name="docling-serve", version="1"),
                engine_fingerprint="ocrmac",
                blocks=[Block(type="paragraph", text="scanned text")],
                markdown="scanned text",
                metadata=DocMeta(ocr_used=True),
            )

    sink = FakeSink()
    item = _item(source="docs", native_id="s.pdf", mime_type="application/pdf",
                 payload=BlobPayload(data=b"%PDF"))
    _pipeline(sink, OcrExtractor()).index_source(FakeSource("docs", [item]))
    assert sink.docs and all(d.extract_sig == "docling-serve:ocrmac" for d in sink.docs)
    assert all(d.ocr_used for d in sink.docs)


def test_legacy_cached_result_sig_falls_back_to_current_fingerprint():
    # Results cached before engine_fingerprint existed deserialize with None; the sig then
    # falls back to the producing engine's CURRENT fingerprint (FakeExtractor stamps nothing).
    sink, ex = FakeSink(), FakeExtractor()
    item = _item(source="docs", native_id="f.pdf", mime_type="application/pdf",
                 payload=BlobPayload(data=b"PDFBYTES"))
    _pipeline(sink, ex).index_source(FakeSource("docs", [item]))
    assert sink.docs and all(d.extract_sig == "fake:" for d in sink.docs)


class _SigEngine(FakeExtractor):
    """Extractor whose fingerprint and output are parameterized per test."""

    def __init__(self, fingerprint, text):
        super().__init__()
        self.fingerprint = fingerprint
        self._text = text

    def extract(self, req):
        self.calls += 1
        return ExtractionResult(
            engine=EngineRef(name="fake", version="1"),
            engine_fingerprint=self.fingerprint,
            blocks=[Block(type="paragraph", text=self._text)],
            markdown=self._text,
        )


def test_full_tree_reconcile_reparses_on_extract_sig_drift():
    # File unchanged on disk, but its indexed text was produced under superseded extraction
    # settings: the reconcile must re-parse it, bypassing the cache read (the cached row IS
    # the stale artifact — same content hash, same engine name/version, same options).
    stat = FileStat(2, 100.0)
    disk = {"a": (stat, b"aa")}
    cache = DictCache()
    src = FakeTreeSource("docs", disk, mime="application/pdf")
    # Seed the cache under the OLD settings.
    Pipeline(FundusConfig(), FakeSink(), _SigEngine("OLD", "OLD TEXT"), cache, DictState()).index_source(
        src, full=True
    )
    sink = FakeSink(manifest={"a": ManifestEntry(stat, "fake:OLD", False)})
    ex = _SigEngine("NEW", "NEW TEXT")
    Pipeline(FundusConfig(), sink, ex, cache, DictState()).index_source(src, full=True)
    assert ex.calls == 1  # the cached OLD row was bypassed, not served
    assert any("NEW TEXT" in d.body for d in sink.docs)
    assert all(d.extract_sig == "fake:NEW" for d in sink.docs)
    assert sink.deleted_parents[0] == {parent_id("docs", "a")}  # old chunks cleared


def test_full_tree_reconcile_ignores_missing_and_foreign_sigs():
    # Pre-feature docs (empty sig) and docs produced by an engine the current extractor
    # doesn't know must NOT re-parse: they converge on their natural re-index or via reparse.
    stat = FileStat(2, 100.0)
    disk = {"a": (stat, b"aa"), "b": (stat, b"bb")}
    sink = FakeSink(manifest={
        "a": ManifestEntry(stat, "", False),
        "b": ManifestEntry(stat, "someother:xyz", False),
    })
    ex = FakeExtractor()
    Pipeline(FundusConfig(), sink, ex, DictCache(), DictState()).index_source(
        FakeTreeSource("docs", disk, mime="application/pdf"), full=True
    )
    assert ex.calls == 0 and sink.docs == []


def test_reparse_bypasses_cache_and_replaces_docs():
    stat = FileStat(2, 100.0)
    disk = {"a": (stat, b"aa"), "b": (stat, b"bb")}
    src = FakeTreeSource("docs", disk, mime="application/pdf")
    cache, ex = DictCache(), FakeExtractor()
    Pipeline(FundusConfig(), FakeSink(), ex, cache, DictState()).index_source(src, full=True)
    seeded = ex.calls

    sink = FakeSink()
    counts = Pipeline(FundusConfig(), sink, ex, cache, DictState()).reparse(src, ["a"])
    assert ex.calls == seeded + 1  # re-extracted "a" despite its cache row
    assert counts["reparsed"] == 1 and counts["selected"] == 1 and counts["chunks"] >= 1
    assert {d.native_id for d in sink.docs} == {"a"}
    assert sink.deleted_parents == [{parent_id("docs", "a")}]


def test_reparse_keeps_old_docs_when_extraction_fails():
    stat = FileStat(2, 100.0)
    src = FakeTreeSource("docs", {"a": (stat, b"aa")}, mime="application/pdf")

    class Broken(FakeExtractor):
        def extract(self, req):
            self.calls += 1
            raise RuntimeError("engine down")

    sink = FakeSink()
    counts = Pipeline(
        FundusConfig(), sink, Broken(), DictCache(), DictState(), retry_backoff=0.0
    ).reparse(src, ["a"])
    assert counts["failed"] == 1 and counts["reparsed"] == 0
    # Nothing deleted and nothing upserted: the old chunks survive an engine outage.
    assert sink.deleted_parents == [] and sink.docs == []


def test_reparse_counts_vanished_files():
    src = FakeTreeSource("docs", {}, mime="application/pdf")
    counts = Pipeline(FundusConfig(), FakeSink(), FakeExtractor(), DictCache(), DictState()).reparse(
        src, ["gone"]
    )
    assert counts == {"selected": 1, "reparsed": 0, "missing": 1, "failed": 0, "chunks": 0}


def test_engine_detected_languages_reach_the_index_doc():
    class LangExtractor(FakeExtractor):
        def extract(self, req):
            self.calls += 1
            return ExtractionResult(
                engine=EngineRef(name="fake", version="1"),
                blocks=[Block(type="paragraph", text="hallo welt")],
                markdown="hallo welt",
                metadata=DocMeta(languages=["de"]),
            )

    sink = FakeSink()
    item = _item(source="docs", native_id="f.pdf", mime_type="application/pdf",
                 payload=BlobPayload(data=b"PDFBYTES"))
    _pipeline(sink, LangExtractor()).index_source(FakeSource("docs", [item]))
    assert sink.docs and all(d.lang == ["de"] for d in sink.docs)
