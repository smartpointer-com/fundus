from types import SimpleNamespace

from fundus.index.base import IndexSettings
from fundus.index.meili import MeiliSink
from fundus.models import FileStat, IndexDocument, ManifestEntry


class FakeIndex:
    def __init__(self, search_response=None, documents=None):
        self.settings = None
        self.added: list[list[dict]] = []
        self.deleted: list[str] = []
        self.last_params: dict | None = None
        self.doc_queries: list[dict] = []
        self._resp = search_response or {}
        self._documents = documents or []  # rows returned by get_documents

    def update_settings(self, payload):
        self.settings = payload
        return {"taskUid": 1}

    def add_documents(self, docs, primary_key=None):
        self.added.append(list(docs))
        return {"taskUid": 1}

    def delete_documents(self, ids=None, *, filter=None):  # mirrors the real client's signature
        self.deleted.append(filter)
        return {"taskUid": 1}

    def search(self, query, params=None):
        self.last_params = params
        return self._resp

    def get_documents(self, params=None):
        self.doc_queries.append(params or {})
        offset = (params or {}).get("offset", 0)
        limit = (params or {}).get("limit", 20)
        return SimpleNamespace(results=self._documents[offset : offset + limit])


class FakeClient:
    def __init__(self, index):
        self._index = index
        self.created: list[tuple] = []

    def create_index(self, uid, opts=None):
        self.created.append((uid, opts))
        return {"taskUid": 1}

    def index(self, uid):
        return self._index


def _docs(n):
    return [
        IndexDocument(id=str(i), source="s", item_kind="file", parent_id=f"p{i}", body="x")
        for i in range(n)
    ]


def test_ensure_schema_creates_and_sets_settings():
    idx = FakeIndex()
    client = FakeClient(idx)
    sink = MeiliSink(index="corpus", embedders={"default": {"source": "rest"}}, client=client)
    sink.ensure_schema(IndexSettings())
    assert client.created and client.created[0][0] == "corpus"
    assert idx.settings["embedders"]["default"]["source"] == "rest"


def test_upsert_batches():
    idx = FakeIndex()
    sink = MeiliSink(index="c", client=FakeClient(idx), batch_size=3)
    sink.upsert(_docs(7))
    assert [len(b) for b in idx.added] == [3, 3, 1]


def test_upsert_lifts_vector_into_meili_vectors():
    idx = FakeIndex()
    sink = MeiliSink(index="c", client=FakeClient(idx))
    doc = IndexDocument(
        id="1", source="s", item_kind="file", parent_id="p", body="x", vector=[0.1, 0.2, 0.3]
    )
    sink.upsert([doc])
    rec = idx.added[0][0]
    assert rec["_vectors"] == {"default": [0.1, 0.2, 0.3]}
    assert "vector" not in rec  # the raw field never reaches Meili


def test_upsert_omits_vectors_when_unset():
    idx = FakeIndex()
    sink = MeiliSink(index="c", client=FakeClient(idx))
    sink.upsert(_docs(1))
    rec = idx.added[0][0]
    assert "_vectors" not in rec and "vector" not in rec


def test_delete_missing():
    idx = FakeIndex(
        search_response={"facetDistribution": {"parent_id": {"p1": 2, "p2": 1, "p3": 5}}}
    )
    sink = MeiliSink(index="c", client=FakeClient(idx))
    n = sink.delete_missing("s", live_parent_ids={"p2"})
    assert n == 2
    assert idx.deleted and "parent_id IN" in idx.deleted[0]
    assert '"p1"' in idx.deleted[0] and '"p3"' in idx.deleted[0]


def test_sink_only_calls_methods_the_real_client_has():
    # Guards against the FakeIndex drifting from meilisearch's real API — a drifted fake can hide
    # calls to methods the real client lacks. Every Index method the sink relies on must exist
    # on both the real client and the fake.
    from meilisearch.index import Index

    for method in (
        "add_documents",
        "delete_documents",
        "get_documents",
        "search",
        "update_settings",
    ):
        assert hasattr(Index, method), f"real client lost {method}"
        assert hasattr(FakeIndex, method), f"fake missing {method}"


def test_delete_parents_noop_on_empty():
    idx = FakeIndex()
    sink = MeiliSink(index="c", client=FakeClient(idx))
    assert sink.delete_parents(set()) == 0
    assert idx.deleted == []  # no filter-delete issued for an empty set


def test_indexed_manifest_dedupes_and_skips_unfingerprinted():
    docs = [
        {"native_id": "a", "size": 10, "mtime": 100.5, "extract_sig": "tika:", "ocr_used": False},
        {
            "native_id": "a",
            "size": 10,
            "mtime": 100.5,
            "extract_sig": "tika:",
        },  # same file, chunk 2
        {
            "native_id": "b",
            "size": 20,
            "mtime": 200.0,
            "extract_sig": "docling-serve:ocrmac",
            "ocr_used": True,
        },
        {"native_id": "c", "size": 0, "mtime": 0.0},  # legacy doc without a fingerprint -> skipped
        {"native_id": "d", "size": 5, "mtime": 50.0},  # pre-extract_sig doc -> empty sig
    ]
    idx = FakeIndex(documents=docs)
    sink = MeiliSink(index="c", client=FakeClient(idx))
    manifest = sink.indexed_manifest("docs")
    assert manifest == {
        "a": ManifestEntry(FileStat(10, 100.5), "tika:", False),
        "b": ManifestEntry(FileStat(20, 200.0), "docling-serve:ocrmac", True),
        "d": ManifestEntry(FileStat(5, 50.0), "", False),
    }
    assert idx.doc_queries[0]["filter"] == 'source = "docs"'
    assert (
        "extract_sig" in idx.doc_queries[0]["fields"] and "ocr_used" in idx.doc_queries[0]["fields"]
    )


def test_indexed_manifest_paginates():
    docs = [{"native_id": f"f{i}", "size": 1, "mtime": float(i)} for i in range(25)]
    idx = FakeIndex(documents=docs)
    sink = MeiliSink(index="c", client=FakeClient(idx))
    sink._fingerprint_page = 10  # small page size to force pagination
    manifest = sink.indexed_manifest("docs")
    assert len(manifest) == 25
    assert len(idx.doc_queries) == 3  # 10 + 10 + 5


def test_search_groups_by_parent():
    hits = {
        "hits": [
            {"id": "1", "parent_id": "A", "title": "ta"},
            {"id": "2", "parent_id": "A", "title": "ta"},
            {"id": "3", "parent_id": "B", "title": "tb"},
        ]
    }
    sink = MeiliSink(index="c", client=FakeClient(FakeIndex(search_response=hits)))
    groups = sink.search("q")
    assert [g["parent_id"] for g in groups] == ["A", "B"]
    assert len(groups[0]["chunks"]) == 2


class FakeQueryEmbedder:
    def __init__(self):
        self.calls: list[str] = []

    def embed_query(self, query):
        self.calls.append(query)
        return [0.5, 0.5]


def test_search_self_embeds_query_when_embedder_present():
    idx = FakeIndex(search_response={"hits": [{"id": "1", "parent_id": "A"}]})
    qe = FakeQueryEmbedder()
    sink = MeiliSink(index="c", client=FakeClient(idx), query_embedder=qe)
    sink.search("hello", semantic_ratio=0.5)
    assert qe.calls == ["hello"]
    assert idx.last_params["vector"] == [0.5, 0.5]


def test_search_skips_query_embed_when_keyword_only():
    idx = FakeIndex(search_response={"hits": []})
    qe = FakeQueryEmbedder()
    sink = MeiliSink(index="c", client=FakeClient(idx), query_embedder=qe)
    sink.search("hello", semantic_ratio=0.0)
    assert qe.calls == []
    assert "vector" not in idx.last_params
