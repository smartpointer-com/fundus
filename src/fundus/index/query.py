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
        "showRankingScore": True,  # surface a 0..1 relevance score per hit
        "attributesToCrop": ["body"],  # a snippet around the match instead of the whole chunk
        "cropLength": 40,
        "cropMarker": "…",
    }
    if filters:
        params["filter"] = filters
    return params


def group_by_parent(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ranked chunk hits to parent artifacts, preserving rank order.

    Each group carries the fields needed to act on a result: a ``ref`` follow-up handle
    (native id / path), timestamp, source/kind, score, and a matched-text ``snippet``.
    """
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for hit in hits:
        pid = hit.get("parent_id") or hit.get("id", "")
        if pid not in grouped:
            grouped[pid] = {
                "parent_id": pid,
                "source": hit.get("source"),
                "item_kind": hit.get("item_kind"),
                "title": hit.get("title"),
                "ref": hit.get("native_id") or hit.get("path"),
                "path": hit.get("path"),
                "ts": hit.get("ts"),
                "actors": hit.get("actors"),
                "tags": hit.get("tags"),
                "mime": hit.get("mime"),
                "msg_ids": hit.get("msg_ids"),
                "score": hit.get("_rankingScore"),
                "snippet": (hit.get("_formatted") or {}).get("body") or (hit.get("body") or "")[:240],
                "chunks": [],
            }
            order.append(pid)
        group = grouped[pid]
        group["chunks"].append(hit)
        score = hit.get("_rankingScore")
        if score is not None and (group["score"] is None or score > group["score"]):
            group["score"] = score
    return [grouped[p] for p in order]
