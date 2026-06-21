from fundus.index.base import IndexSettings
from fundus.index.meili import MeiliSink
from fundus.models import IndexDocument


class FakeIndex:
    def __init__(self, search_response=None):
        self.settings = None
        self.added: list[list[dict]] = []
        self.deleted: list[str] = []
        self.last_params: dict | None = None
        self._resp = search_response or {}

    def update_settings(self, payload):
        self.settings = payload
        return {"taskUid": 1}

    def add_documents(self, docs, primary_key=None):
        self.added.append(list(docs))
        return {"taskUid": 1}

    def delete_documents_by_filter(self, flt):
        self.deleted.append(flt)
        return {"taskUid": 1}

    def search(self, query, params=None):
        self.last_params = params
        return self._resp


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


def test_delete_missing():
    idx = FakeIndex(search_response={"facetDistribution": {"parent_id": {"p1": 2, "p2": 1, "p3": 5}}})
    sink = MeiliSink(index="c", client=FakeClient(idx))
    n = sink.delete_missing("s", live_parent_ids={"p2"})
    assert n == 2
    assert idx.deleted and "parent_id IN" in idx.deleted[0]
    assert '"p1"' in idx.deleted[0] and '"p3"' in idx.deleted[0]


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
