"""Build the Meilisearch REST-embedder configuration.

Points Meilisearch at a bare-metal, OpenAI-compatible ``/embeddings`` endpoint so
it embeds documents at index time and queries at search time. The orchestrator
never calls the model itself.
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
        # sends one HTTP request per document — ~27x slower against a high-latency local model.
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
