import json

import httpx

from fundus.embed.client import EmbeddingClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embed_posts_model_and_input():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    client = EmbeddingClient("http://e/v1/embeddings", "m", client=_client(handler))
    assert client.embed(["hello"]) == [[0.1, 0.2]]
    assert captured == {"model": "m", "input": ["hello"]}


def test_embed_query_applies_prefix_and_auth():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"embedding": [1.0]}]})

    client = EmbeddingClient(
        "http://e",
        "m",
        api_key="sekret",
        query_prompt="Instruct: x\nQuery: ",
        client=_client(handler),
    )
    assert client.embed_query("tax docs") == [1.0]
    assert captured["body"]["input"] == ["Instruct: x\nQuery: tax docs"]
    assert captured["auth"] == "Bearer sekret"


def test_embed_query_no_prefix_when_empty():
    def handler(request):
        assert json.loads(request.content)["input"] == ["plain"]
        return httpx.Response(200, json={"data": [{"embedding": [0.0]}]})

    EmbeddingClient("http://e", "m", client=_client(handler)).embed_query("plain")
