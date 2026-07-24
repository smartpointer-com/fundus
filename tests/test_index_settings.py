import pytest

from fundus.config import EmbedderConfig
from fundus.embed.config import rest_embedder, user_provided_embedder
from fundus.index.base import IndexSettings
from fundus.index.settings import build_index_settings


def test_user_provided_embedder():
    emb = user_provided_embedder(EmbedderConfig(url="http://x", model="m", dimensions=2560))
    assert emb["default"] == {"source": "userProvided", "dimensions": 2560}


def test_user_provided_embedder_requires_dimensions():
    with pytest.raises(ValueError, match="dimensions"):
        user_provided_embedder(EmbedderConfig(url="http://x", model="m"))


def test_build_index_settings():
    payload = build_index_settings(IndexSettings(locales=["eng", "deu"]))
    assert payload["searchableAttributes"] == ["title", "body"]
    assert "ts" in payload["filterableAttributes"]
    assert payload["sortableAttributes"] == ["ts"]
    assert payload["localizedAttributes"] == [
        {"locales": ["eng", "deu"], "attributePatterns": ["*"]}
    ]
    assert "embedders" not in payload


def test_build_index_settings_with_embedders():
    emb = rest_embedder(EmbedderConfig(url="http://x/v1/embeddings", model="m"))
    payload = build_index_settings(IndexSettings(), emb)
    assert payload["embedders"]["default"]["source"] == "rest"


def test_rest_embedder_fields():
    emb = rest_embedder(EmbedderConfig(url="http://x/v1/embeddings", model="qwen", dimensions=1024))
    default = emb["default"]
    assert default["url"] == "http://x/v1/embeddings"
    assert default["request"]["model"] == "qwen"
    assert default["dimensions"] == 1024
    assert "{{doc.title}}" in default["documentTemplate"]
    # batch markers: many docs per request, parallel array of embeddings back
    assert default["request"]["input"] == ["{{text}}", "{{..}}"]
    assert default["response"]["data"] == [{"embedding": "{{embedding}}"}, "{{..}}"]


def test_rest_embedder_includes_api_key_when_set():
    emb = rest_embedder(EmbedderConfig(url="http://x", model="m", api_key="secret"))
    assert emb["default"]["apiKey"] == "secret"


def test_rest_embedder_omits_api_key_when_unset():
    emb = rest_embedder(EmbedderConfig(url="http://x", model="m"))
    assert "apiKey" not in emb["default"]
