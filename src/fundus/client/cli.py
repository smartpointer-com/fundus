"""`fundus-client` — a thin MCP client that calls a running fundus server's tools.

For interactive use and shell scripts. Holds no Meili/embed keys — only the server endpoint and the
bearer token. Talks streamable-HTTP (or SSE) to the server and invokes its search/sources/locate
tools, so it is a true read-only consumer just like an agent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import typer

from fundus import __version__
from fundus.cli_common import ConfigOpt
from fundus.config import FundusConfig, load_config
from fundus.render import render_groups

client_app = typer.Typer(no_args_is_help=True, help="Query a running fundus MCP server.")

UrlOpt = typer.Option(None, "--url", help="Server endpoint (default: from [serve] config).")
TokenOpt = typer.Option(None, "--token", help="Bearer token (default: [serve].token / env).")
TransportOpt = typer.Option(None, "--transport", help="streamable-http (default) | sse.")


@client_app.command()
def version() -> None:
    """Print the fundus-client version."""
    typer.echo(f"v{__version__}")


def _payload(result: Any) -> Any:
    """Extract a tool's return value from a CallToolResult (structured first, else parse text)."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured["result"] if set(structured) == {"result"} else structured
    items: list[Any] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            items.append(json.loads(text))
        except json.JSONDecodeError:
            items.append(text)
    return items[0] if len(items) == 1 else items


async def _call(endpoint: str, token: str | None, transport: str, tool: str, args: dict[str, Any]) -> Any:
    from mcp import ClientSession

    headers = {"Authorization": f"Bearer {token}"} if token else None
    if transport == "sse":
        from mcp.client.sse import sse_client

        cm = sse_client(endpoint, headers=headers)
    else:
        from mcp.client.streamable_http import streamablehttp_client

        cm = streamablehttp_client(endpoint, headers=headers)
    async with cm as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            payload = {k: v for k, v in args.items() if v is not None}
            return _payload(await session.call_tool(tool, payload))


def _resolve(cfg: FundusConfig, url: str | None, token: str | None, transport: str | None) -> tuple[str, str | None, str]:
    tr = transport or cfg.serve.transport
    if tr == "stdio":
        raise typer.BadParameter("the client needs an HTTP transport (streamable-http or sse)")
    return url or cfg.serve.endpoint(), token or cfg.serve.token, tr


def _unwrap(exc: BaseException) -> BaseException:
    """Dig the first leaf out of anyio's nested ExceptionGroups for a readable message."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _failure_message(endpoint: str, has_token: bool, reason: object) -> str:
    """Turn a transport/auth error into an actionable one-liner."""
    text = str(reason).lower()
    if "401" in text or "unauthorized" in text:
        if not has_token:
            return f"unauthorized ({endpoint}): no bearer token sent — set FUNDUS_SERVE_TOKEN or pass --token"
        return (f"unauthorized ({endpoint}): the server rejected the token — make sure it matches the "
                f"server's FUNDUS_SERVE_TOKEN")
    return f"request to {endpoint} failed: {reason}"


def _invoke(cfg: FundusConfig, url: str | None, token: str | None, transport: str | None, tool: str, args: dict[str, Any]) -> Any:
    endpoint, tok, tr = _resolve(cfg, url, token, transport)
    try:
        return asyncio.run(_call(endpoint, tok, tr, tool, args))
    except Exception as exc:  # noqa: BLE001 - surface any transport/auth failure as a clean message
        typer.echo(f"fundus-client: {_failure_message(endpoint, bool(tok), _unwrap(exc))}", err=True)
        raise typer.Exit(code=1) from None


@client_app.command()
def query(
    text: str,
    limit: int = typer.Option(10, help="Maximum results."),
    semantic_ratio: float = typer.Option(0.5, help="0=keyword, 1=semantic."),
    filters: str | None = typer.Option(None, help="Meilisearch filter expression."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Emit JSON."),
    url: str | None = UrlOpt,
    token: str | None = TokenOpt,
    transport: str | None = TransportOpt,
    config: Path | None = ConfigOpt,
) -> None:
    """Search the corpus via the server's `search` tool."""
    groups = _invoke(load_config(config), url, token, transport, "search",
                     {"query": text, "limit": limit, "semantic_ratio": semantic_ratio, "filters": filters})
    render_groups(groups, json_out=json_out)


@client_app.command()
def sources(
    json_out: bool = typer.Option(False, "--json", "-j", help="Emit JSON."),
    url: str | None = UrlOpt,
    token: str | None = TokenOpt,
    transport: str | None = TransportOpt,
    config: Path | None = ConfigOpt,
) -> None:
    """List indexed sources and document counts via the server's `sources` tool."""
    rows = _invoke(load_config(config), url, token, transport, "sources", {})
    if json_out:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    for s in rows or []:
        where = s.get("roots") or s.get("db") or ""
        typer.echo(f"{s.get('name')}  ({s.get('type')})  {s.get('indexed_docs', 0)} docs  {where}")


@client_app.command()
def locate(
    ref: str,
    json_out: bool = typer.Option(False, "--json", "-j", help="Emit JSON."),
    url: str | None = UrlOpt,
    token: str | None = TokenOpt,
    transport: str | None = TransportOpt,
    config: Path | None = ConfigOpt,
) -> None:
    """Resolve a search hit's `ref` to an openable path via the server's `locate` tool."""
    res = _invoke(load_config(config), url, token, transport, "locate", {"ref": ref})
    typer.echo(json.dumps(res, indent=2, default=str) if json_out else res)
