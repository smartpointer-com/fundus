"""Hybrid search parameters and grouping search hits back to their parents."""

from __future__ import annotations

from typing import Any

from fundus.embed.config import DEFAULT_EMBEDDER


def hybrid_search_params(
    *,
    semantic_ratio: float = 0.5,
    filters: str | None = None,
    limit: int = 20,
    embedder: str = DEFAULT_EMBEDDER,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "limit": limit,
        "hybrid": {"semanticRatio": semantic_ratio, "embedder": embedder},
    }
    if filters:
        params["filter"] = filters
    return params


def group_by_parent(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ranked chunk hits to parent artifacts, preserving rank order."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hit in hits:
        pid = hit.get("parent_id") or hit.get("id", "")
        if pid not in grouped:
            grouped[pid] = {
                "parent_id": pid,
                "source": hit.get("source"),
                "title": hit.get("title"),
                "chunks": [],
            }
            order.append(pid)
        grouped[pid]["chunks"].append(hit)
    return [grouped[p] for p in order]
