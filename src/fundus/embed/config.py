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
        "request": {"model": cfg.model, "input": ["{{text}}"]},
        "response": {"data": [{"embedding": "{{embedding}}"}]},
        "documentTemplate": document_template,
    }
    if cfg.dimensions:
        embedder["dimensions"] = cfg.dimensions
    if cfg.api_key:
        # Meilisearch's REST embedder sends this as `Authorization: Bearer <key>`.
        embedder["apiKey"] = cfg.api_key
    return {name: embedder}
