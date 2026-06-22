"""Backfill the embedding cache from vectors already stored in the search index.

A one-time bootstrap so the first re-run after introducing the cache doesn't recompute every
vector — it reads each document's title/body and its stored vector and writes a (text -> vector)
cache entry. Idempotent; safe to re-run.
"""

from __future__ import annotations

from typing import Any

import httpx

from fundus.embed.cache import SqliteEmbeddingCache, embed_input_text


def _extract_vector(vectors: Any, embedder: str) -> list[float] | None:
    if not isinstance(vectors, dict):
        return None
    entry = vectors.get(embedder)
    if isinstance(entry, dict):  # {"embeddings": [[...]], "regenerate": false}
        entry = entry.get("embeddings")
    if isinstance(entry, list) and entry and isinstance(entry[0], list):
        entry = entry[0]  # one vector per doc
    if isinstance(entry, list) and entry and isinstance(entry[0], int | float):
        return [float(x) for x in entry]
    return None


def backfill_embedding_cache(
    *,
    url: str,
    index: str,
    api_key: str | None,
    cache: SqliteEmbeddingCache,
    embedder: str = "default",
    batch: int = 1000,
    client: httpx.Client | None = None,
) -> int:
    """Populate ``cache`` from documents + vectors in the index. Returns vectors written."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    owned = client is None
    client = client or httpx.Client(timeout=120.0, headers=headers)
    written = 0
    offset = 0
    try:
        while True:
            resp = client.post(
                f"{url.rstrip('/')}/indexes/{index}/documents/fetch",
                json={
                    "limit": batch,
                    "offset": offset,
                    "fields": ["title", "body"],
                    "retrieveVectors": True,
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                break
            texts: list[str] = []
            vectors: list[list[float]] = []
            for doc in results:
                vector = _extract_vector(doc.get("_vectors"), embedder)
                if vector:
                    texts.append(embed_input_text(doc.get("title"), doc.get("body", "")))
                    vectors.append(vector)
            if texts:
                cache.put_many(texts, vectors)
                written += len(texts)
            offset += len(results)
            if len(results) < batch:
                break
    finally:
        if owned:
            client.close()
    return written
