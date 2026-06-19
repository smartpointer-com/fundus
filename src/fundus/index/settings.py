"""Build the Meilisearch index-settings payload from IndexSettings."""

from __future__ import annotations

from typing import Any

from fundus.index.base import IndexSettings


def build_index_settings(
    settings: IndexSettings, embedders: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "searchableAttributes": settings.searchable,
        "filterableAttributes": settings.filterable,
        "sortableAttributes": settings.sortable,
        "localizedAttributes": [{"locales": settings.locales, "attributePatterns": ["*"]}],
    }
    if embedders:
        payload["embedders"] = embedders
    return payload
