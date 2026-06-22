import httpx
import pytest

from fundus.embed.backfill import _extract_vector, backfill_embedding_cache
from fundus.embed.cache import SqliteEmbeddingCache, embed_input_text


def test_cache_roundtrip_and_miss(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "e.db"), "m1")
    cache.put_many(["hello", "world"], [[0.1, 0.2], [0.3, 0.4]])
    got = cache.get_many(["world", "missing", "hello"])
    assert got[0] == pytest.approx([0.3, 0.4], abs=1e-6)  # stored as float32
    assert got[1] is None
    assert got[2] == pytest.approx([0.1, 0.2], abs=1e-6)


def test_cache_keyed_by_model(tmp_path):
    path = str(tmp_path / "e.db")
    SqliteEmbeddingCache(path, "m1").put_many(["x"], [[1.0]])
    assert SqliteEmbeddingCache(path, "m2").get_many(["x"]) == [None]  # different model -> miss


def test_embed_input_text():
    assert embed_input_text("Subj", "body") == "Subj body"
    assert embed_input_text(None, "body") == "body"


def test_extract_vector_formats():
    assert _extract_vector({"default": {"embeddings": [[1.0, 2.0]]}}, "default") == [1.0, 2.0]
    assert _extract_vector({"default": [1.0, 2.0]}, "default") == [1.0, 2.0]
    assert _extract_vector({}, "default") is None
    assert _extract_vector(None, "default") is None


def test_backfill_from_index(tmp_path):
    pages = [
        {
            "results": [
                {"title": "A", "body": "a body", "_vectors": {"default": {"embeddings": [[0.1, 0.2]]}}},
                {"title": None, "body": "b body", "_vectors": {"default": [0.3, 0.4]}},
                {"title": "C", "body": "c", "_vectors": {}},  # no vector -> skipped
            ]
        },
        {"results": []},
    ]
    state = {"n": 0}

    def handler(request):
        i = min(state["n"], len(pages) - 1)
        state["n"] += 1
        return httpx.Response(200, json=pages[i])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cache = SqliteEmbeddingCache(str(tmp_path / "e.db"), "m1")
    n = backfill_embedding_cache(
        url="http://x", index="corpus", api_key="k", cache=cache, client=client
    )
    assert n == 2
    got = cache.get_many([embed_input_text("A", "a body"), embed_input_text(None, "b body")])
    assert got[0] == pytest.approx([0.1, 0.2], abs=1e-6)
    assert got[1] == pytest.approx([0.3, 0.4], abs=1e-6)
