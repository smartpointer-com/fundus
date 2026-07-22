"""Build Meilisearch embedder configurations for a bare-metal, OpenAI-compatible endpoint.

Two shapes: ``rest_embedder`` lets Meilisearch embed documents itself at index time (the
orchestrator only embeds queries), while ``user_provided_embedder`` is used for fan-out indexing,
where Fundus computes the document vectors and hands Meilisearch only the finished vectors.
"""

from __future__ import annotations

from typing import Any

from fundus.config import EmbedderConfig

DEFAULT_EMBEDDER = "default"


def rest_embedder(
    cfg: EmbedderConfig,
    *,
    name: str = DEFAULT_EMBEDDER,
    document_template: str = "{{doc.title}} {{doc.body}}",
) -> dict[str, Any]:
    embedder: dict[str, Any] = {
        "source": "rest",
        "url": cfg.url,
        # The "{{..}}" rest markers let Meilisearch batch many documents into one request
        # (input array) and read back the parallel array of embeddings. Without them Meili
        # sends one HTTP request per document — over an order of magnitude slower against a
        # heavy local model.
        "request": {"model": cfg.model, "input": ["{{text}}", "{{..}}"]},
        "response": {"data": [{"embedding": "{{embedding}}"}, "{{..}}"]},
        "documentTemplate": document_template,
    }
    if cfg.dimensions:
        embedder["dimensions"] = cfg.dimensions
    if cfg.api_key:
        # Meilisearch's REST embedder sends this as `Authorization: Bearer <key>`.
        embedder["apiKey"] = cfg.api_key
    return {name: embedder}


def user_provided_embedder(cfg: EmbedderConfig, *, name: str = DEFAULT_EMBEDDER) -> dict[str, Any]:
    """A ``userProvided`` embedder: Meili stores vectors Fundus supplies and embeds nothing itself.

    Used with fan-out indexing, where Fundus computes document vectors concurrently. Meilisearch
    requires the dimension count up front for this source.
    """
    if not cfg.dimensions:
        raise ValueError("fanout embedding requires embedder.dimensions to be set")
    return {name: {"source": "userProvided", "dimensions": cfg.dimensions}}
