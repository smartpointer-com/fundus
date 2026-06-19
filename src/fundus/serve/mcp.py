"""MCP server exposing read-only search to agents.

The consumer (an agent) gets a single ``search`` tool backed by the read-only
search key. ``search_tool`` holds the logic (testable); ``run_server`` wires it
into an MCP stdio server.
"""

from __future__ import annotations

from typing import Any

from fundus.config import FundusConfig
from fundus.core.pipeline import build_sink


def search_tool(
    sink: Any,
    query: str,
    *,
    limit: int = 20,
    semantic_ratio: float = 0.5,
    filters: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search; returns artifacts grouped by parent."""
    results: list[dict[str, Any]] = sink.search(
        query, semantic_ratio=semantic_ratio, filters=filters, limit=limit
    )
    return results


def build_mcp(sink: Any) -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("fundus")

    @server.tool()
    def search(
        query: str,
        limit: int = 20,
        semantic_ratio: float = 0.5,
        filters: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search the indexed corpus (email, chat, documents). Returns matching
        artifacts grouped by their parent, ranked by hybrid keyword+semantic relevance."""
        return search_tool(
            sink, query, limit=limit, semantic_ratio=semantic_ratio, filters=filters
        )

    return server


def run_server(config: FundusConfig) -> None:
    build_mcp(build_sink(config)).run()
