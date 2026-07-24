"""MCP server exposing read-only corpus access to agents.

Three tools: ``search`` (hybrid keyword+semantic), ``sources`` (what's indexed and where), and
``locate`` (resolve a hit's ``ref`` to an openable path). Backed by a search-key-only sink, so it
cannot mutate the index. Over HTTP it serves streamable-HTTP (durable) or SSE (legacy), gated by a
bearer token; stdio is available for a host-spawned local subprocess. The ``*_tool`` functions hold
the logic (testable); ``build_mcp``/``run_server`` wire them to the transport.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from pydantic import Field

from fundus.config import FundusConfig
from fundus.core.pipeline import build_search_sink


def _compile_filter(
    source: str | None,
    item_kind: str | None,
    since: int | None,
    until: int | None,
    filters: str | None,
) -> str | None:
    """Combine the typed convenience params and any raw expression into one Meilisearch filter
    (ANDed). The typed params spare the caller from writing Meili's filter DSL for the common cases;
    the failure they prevent is a model reaching for ``source:mail`` (Lucene) instead of Meili's
    ``source = "mail"``."""
    clauses: list[str] = []
    if source:
        clauses.append(f'source = "{source}"')
    if item_kind:
        clauses.append(f'item_kind = "{item_kind}"')
    if since is not None:
        clauses.append(f"ts >= {int(since)}")
    if until is not None:
        clauses.append(f"ts <= {int(until)}")
    if filters:
        clauses.append(f"({filters})")
    return " AND ".join(clauses) or None


def search_tool(
    sink: Any,
    query: str,
    *,
    limit: int = 20,
    semantic_ratio: float = 0.5,
    source: str | None = None,
    item_kind: str | None = None,
    since: int | None = None,
    until: int | None = None,
    filters: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid search; returns artifacts grouped by parent, each with ref/path/ts/score/snippet."""
    combined = _compile_filter(source, item_kind, since, until, filters)
    try:
        results: list[dict[str, Any]] = sink.search(
            query, semantic_ratio=semantic_ratio, filters=combined, limit=limit
        )
    except Exception as exc:  # turn a bad filter into self-correcting guidance
        if combined and "filter" in str(exc).lower():
            raise ValueError(
                f"invalid filter {combined!r}. Use Meilisearch syntax — "
                'source = "mail", item_kind IN ["file","email"], ts > 1735689600 — not '
                "`field:value`. Simpler: pass the source / item_kind / since / until parameters "
                "instead of a raw filter."
            ) from exc
        raise
    return results


def sources_tool(sink: Any, config: FundusConfig) -> list[dict[str, Any]]:
    """Each configured source with its corpus location and indexed document count."""
    counts = sink.source_counts()
    out: list[dict[str, Any]] = []
    for s in config.sources:
        data = s.model_dump()
        out.append(
            {
                "name": s.name,
                "type": s.type,
                "roots": data.get("roots"),
                "db": data.get("db"),
                "indexed_docs": int(counts.get(s.name, 0)),
            }
        )
    return out


def _notmuch_locate(config: FundusConfig, ref: str) -> list[str]:
    from fundus.sources.registry import build_source

    for cfg in config.sources:
        if cfg.type == "notmuch":
            locate = getattr(build_source(cfg), "locate", None)
            if locate is not None:
                return list(locate(ref))
    return []


def locate_tool(config: FundusConfig, ref: str) -> dict[str, Any]:
    """Resolve a search hit's ``ref`` to something openable: a file path passes through; an email
    Message-ID resolves to its maildir file(s) via notmuch."""
    if ref.startswith("/"):
        return {"kind": "file", "path": ref, "exists": os.path.exists(ref)}
    files = _notmuch_locate(config, ref)
    if files:
        return {"kind": "email", "message_id": ref, "files": files}
    return {"kind": "unknown", "ref": ref}


def build_mcp(sink: Any, config: FundusConfig) -> Any:
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("fundus", host=config.serve.host, port=config.serve.port)

    @server.tool()
    def search(
        query: str,
        limit: int = 20,
        semantic_ratio: float = 0.5,
        source: Annotated[
            str | None,
            Field(
                description=(
                    'Restrict to ONE indexed source by name (e.g. "mail", "chat", "documents"); call '
                    "`sources` for the exact names. Compiled to a filter for you — prefer this over `filters`."
                )
            ),
        ] = None,
        item_kind: Annotated[
            str | None,
            Field(description=('Restrict to one item kind, e.g. "file", "email", "chat_window".')),
        ] = None,
        since: Annotated[
            int | None, Field(description="Only items at/after this Unix epoch time (seconds).")
        ] = None,
        until: Annotated[
            int | None, Field(description="Only items at/before this Unix epoch time (seconds).")
        ] = None,
        filters: Annotated[
            str | None,
            Field(
                description=(
                    "Advanced: a raw Meilisearch filter, ANDed with the params above. Meili syntax only — "
                    'source = "mail", item_kind IN ["file","email"], ts > 1735689600 — NOT `field:value` '
                    "(no colons). Filterable fields: source, item_kind, ts, actors, tags, path, mime, lang."
                )
            ),
        ] = None,
    ) -> list[dict[str, Any]]:
        """Search the indexed corpus (email, chat, documents). Returns matching artifacts grouped
        by parent and ranked by hybrid keyword+semantic relevance; each carries a ref/path to open.
        To narrow results, PREFER the typed source/item_kind/since/until parameters; use `filters`
        only for advanced expressions."""
        return search_tool(
            sink,
            query,
            limit=limit,
            semantic_ratio=semantic_ratio,
            source=source,
            item_kind=item_kind,
            since=since,
            until=until,
            filters=filters,
        )

    @server.tool()
    def sources() -> list[dict[str, Any]]:
        """List the indexed sources (name, type, corpus roots) with their document counts."""
        return sources_tool(sink, config)

    @server.tool()
    def locate(ref: str) -> dict[str, Any]:
        """Resolve a search hit's `ref` (a file path or an email Message-ID) to an openable
        location on disk for further processing."""
        return locate_tool(config, ref)

    return server


def run_server(
    config: FundusConfig,
    *,
    transport: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    if host:
        config.serve.host = host
    if port:
        config.serve.port = port
    transport = transport or config.serve.transport
    server = build_mcp(build_search_sink(config), config)

    if transport == "stdio":
        server.run("stdio")
        return

    if not config.serve.token:
        raise RuntimeError(
            "the HTTP transports require a bearer token: set [serve].token or FUNDUS_SERVE_TOKEN"
        )
    import uvicorn

    from fundus.serve.auth import bearer_guard

    app = server.sse_app() if transport == "sse" else server.streamable_http_app()
    uvicorn.run(
        bearer_guard(app, config.serve.token), host=config.serve.host, port=config.serve.port
    )
