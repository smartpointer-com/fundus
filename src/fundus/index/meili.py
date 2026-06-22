"""Meilisearch implementation of the Sink interface, plus hybrid search."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from typing import Any

import meilisearch

from fundus.embed.client import EmbeddingClient
from fundus.embed.config import DEFAULT_EMBEDDER
from fundus.index.base import IndexSettings
from fundus.index.query import group_by_parent, hybrid_search_params
from fundus.index.settings import build_index_settings
from fundus.models import IndexDocument


class MeiliSink:
    """Writes index documents to Meilisearch and runs hybrid queries.

    A ``client`` may be injected (for tests); otherwise one is built from ``url``.
    When a ``query_embedder`` is provided, queries are embedded by Fundus (so a
    model-specific instruct prefix can be applied) and passed to Meilisearch as the
    query vector, leaving the keyword query clean.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:7700",
        index: str = "corpus",
        api_key: str | None = None,
        embedders: dict[str, Any] | None = None,
        embedder_name: str = DEFAULT_EMBEDDER,
        client: Any | None = None,
        batch_size: int = 1000,
        query_embedder: EmbeddingClient | None = None,
    ) -> None:
        self._client = client if client is not None else meilisearch.Client(url, api_key)
        self._uid = index
        self._embedders = embedders
        self._embedder_name = embedder_name
        self._batch_size = batch_size
        self._query_embedder = query_embedder

    @property
    def _index(self) -> Any:
        return self._client.index(self._uid)

    def ensure_schema(self, settings: IndexSettings) -> None:
        with suppress(Exception):  # index may already exist
            self._client.create_index(self._uid, {"primaryKey": "id"})
        self._index.update_settings(build_index_settings(settings, self._embedders))

    def upsert(self, docs: Sequence[IndexDocument]) -> None:
        batch: list[dict[str, Any]] = []
        for doc in docs:
            record = doc.model_dump()
            vector = record.pop("vector", None)
            if vector is not None:
                # Fan-out indexing: hand Meili the pre-computed vector via userProvided embedder.
                record["_vectors"] = {self._embedder_name: vector}
            batch.append(record)
            if len(batch) >= self._batch_size:
                self._index.add_documents(batch, primary_key="id")
                batch = []
        if batch:
            self._index.add_documents(batch, primary_key="id")

    def _indexed_parent_ids(self, source: str) -> set[str]:
        res = self._index.search(
            "", {"filter": f'source = "{source}"', "facets": ["parent_id"], "limit": 0}
        )
        dist = (res.get("facetDistribution") or {}).get("parent_id") or {}
        return set(dist.keys())

    def source_counts(self) -> dict[str, int]:
        """Indexed chunk count per source (facet distribution over ``source``)."""
        res = self._index.search("", {"facets": ["source"], "limit": 0})
        dist = (res.get("facetDistribution") or {}).get("source") or {}
        return {str(k): int(v) for k, v in dist.items()}

    def delete_missing(self, source: str, live_parent_ids: set[str]) -> int:
        dead = self._indexed_parent_ids(source) - live_parent_ids
        if dead:
            quoted = ", ".join(f'"{p}"' for p in sorted(dead))
            self._index.delete_documents_by_filter(f"parent_id IN [{quoted}]")
        return len(dead)

    def search(
        self,
        query: str,
        *,
        semantic_ratio: float = 0.5,
        filters: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params = hybrid_search_params(
            semantic_ratio=semantic_ratio,
            filters=filters,
            limit=limit,
            embedder=self._embedder_name,
        )
        if semantic_ratio > 0 and self._query_embedder is not None:
            params["vector"] = self._query_embedder.embed_query(query)
        res = self._index.search(query, params)
        return group_by_parent(res.get("hits", []))
