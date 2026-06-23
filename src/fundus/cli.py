"""Fundus command-line interface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from fundus.bakeoff.runner import run_bakeoff
from fundus.config import load_config
from fundus.core.locking import AlreadyRunning, run_lock
from fundus.core.pipeline import build_pipeline, build_sink
from fundus.extract.registry import build_extractor
from fundus.render import print_result as _print_result
from fundus.service.cli import service_app

app = typer.Typer(add_completion=False, help="Index and search a heterogeneous corpus.")
app.add_typer(service_app, name="service")

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to fundus.toml (default: XDG).")


@app.command()
def init(config: Path | None = ConfigOpt) -> None:
    """Apply the index schema (settings + embedder) to Meilisearch."""
    cfg = load_config(config)
    build_pipeline(cfg).ensure_schema()
    typer.echo(f"Index '{cfg.meilisearch.index}' schema ensured.")


@app.command()
def index(
    only: str | None = typer.Option(None, help="Limit to a single source by name."),
    full: bool = typer.Option(
        False,
        help="Reconcile against reality: prune deletions, and for file trees re-index edits "
        "(by size+mtime) the cursor can't see.",
    ),
    force: bool = typer.Option(False, help="Ignore saved cursors and re-read everything."),
    workers: int | None = typer.Option(None, help="Override the indexing worker count."),
    config: Path | None = ConfigOpt,
) -> None:
    """Run the ingestion pipeline (incremental by default)."""
    cfg = load_config(config)
    if workers is not None:
        cfg.workers = workers
    try:
        with run_lock(str(cfg.lock_path())):
            counts = build_pipeline(cfg).index(only=only, full=full, force=force)
    except AlreadyRunning:
        typer.echo("Another fundus run holds the lock; skipping.")
        raise typer.Exit(code=0) from None
    for name, n in counts.items():
        typer.echo(f"{name}: {n} chunks")


@app.command()
def query(
    text: str,
    limit: int = typer.Option(20, help="Maximum results."),
    semantic_ratio: float = typer.Option(0.5, help="0=keyword, 1=semantic."),
    filters: str | None = typer.Option(None, help="Meilisearch filter expression."),
    json_out: bool = typer.Option(False, "--json", "-j", help="Emit JSON instead of text."),
    fields: str | None = typer.Option(
        None, "--fields",
        help="Comma-separated fields to show/emit, e.g. score,source,ref,ts,title,snippet,path.",
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Hybrid search. Each result carries a follow-up `ref` (email Message-ID, file path, or chat
    JID), its timestamp, source/kind, relevance score, and a matched snippet. Use --json for
    machine output and --fields to pick columns."""
    cfg = load_config(config)
    groups = build_sink(cfg).search(text, semantic_ratio=semantic_ratio, filters=filters, limit=limit)
    keys = [f.strip() for f in fields.split(",")] if fields else None
    if json_out:
        out: Any = [{k: g.get(k) for k in keys} for g in groups] if keys else groups
        typer.echo(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return
    if not groups:
        typer.echo("(no results)")
        return
    for n, g in enumerate(groups, 1):
        if keys:
            typer.echo("  ".join(f"{k}={g.get(k)}" for k in keys))
        else:
            _print_result(n, g)


@app.command()
def serve(
    transport: str | None = typer.Option(
        None, help="streamable-http (default) | sse | stdio. HTTP transports need a bearer token."
    ),
    host: str | None = typer.Option(None, help="Bind host (default 127.0.0.1)."),
    port: int | None = typer.Option(None, help="Bind port (default 8181)."),
    config: Path | None = ConfigOpt,
) -> None:
    """Run the read-only MCP server (search/sources/locate) for agents."""
    from fundus.serve.mcp import run_server

    run_server(load_config(config), transport=transport, host=host, port=port)


@app.command(name="embed-backfill")
def embed_backfill(config: Path | None = ConfigOpt) -> None:
    """Seed the embedding cache from vectors already in the index, so the next re-index reuses
    them instead of recomputing. One-time bootstrap; the cache self-populates thereafter."""
    from fundus.embed.backfill import backfill_embedding_cache
    from fundus.embed.cache import SqliteEmbeddingCache

    cfg = load_config(config)
    cache = SqliteEmbeddingCache(str(cfg.embed_cache_path()), cfg.embedder.model)
    written = backfill_embedding_cache(
        url=cfg.meilisearch.url,
        index=cfg.meilisearch.index,
        api_key=cfg.meilisearch.api_key,
        cache=cache,
    )
    typer.echo(f"Backfilled {written:,} vectors into the embedding cache.")


@app.command(name="paths")
def paths(
    meili_data: bool = typer.Option(
        False, "--meili-data", help="Print only the Meilisearch data directory (for the Docker stack)."
    ),
    config: Path | None = ConfigOpt,
) -> None:
    """Show where Fundus keeps its data (all under one configurable root)."""
    cfg = load_config(config)
    if meili_data:  # machine-readable: `make up` mounts this as the Meili volume
        typer.echo(str(cfg.meili_dir()))
        return
    rows = [
        ("data root", cfg.data_root()),
        ("meili index", f"{cfg.meili_dir()}  (Docker volume)"),
        ("extraction cache", cfg.extraction_cache_path()),
        ("embedding cache", cfg.embed_cache_path()),
        ("cursors", cfg.cursors_path()),
        ("run lock", cfg.lock_path()),
    ]
    for label, value in rows:
        typer.echo(f"{label + ':':18}{value}")


@app.command(name="sources")
def list_sources(
    json_out: bool = typer.Option(False, "--json", "-j", help="Emit JSON."),
    config: Path | None = ConfigOpt,
) -> None:
    """List configured sources with their type, connector settings, and indexed doc counts."""
    cfg = load_config(config)
    try:
        counts = build_sink(cfg).source_counts()
    except Exception:  # noqa: BLE001 - counts are best-effort (index may be absent/unreachable)
        counts = {}

    def detail(s: Any) -> dict[str, Any]:
        return {
            k: v
            for k, v in s.model_dump(exclude={"name", "type"}).items()
            if v not in (None, "", [], {})
        }

    if json_out:
        rows = [
            {"name": s.name, "type": s.type, "docs": counts.get(s.name, 0), "config": detail(s)}
            for s in cfg.sources
        ]
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    if not cfg.sources:
        typer.echo("(no sources configured)")
        return
    for s in cfg.sources:
        cfgstr = "  ".join(f"{k}={v}" for k, v in detail(s).items())
        typer.echo(f"{s.name:<12} {s.type:<8} {counts.get(s.name, 0):>9,} docs   {cfgstr}")


@app.command()
def bakeoff(
    directory: Path,
    engines: str | None = typer.Option(None, help="Comma-separated engines (default: all configured)."),
    out: Path | None = typer.Option(None, help="Write each engine's Markdown here."),
    config: Path | None = ConfigOpt,
) -> None:
    """Run extraction engines over a sample directory and compare output."""
    cfg = load_config(config)
    names = engines.split(",") if engines else list(cfg.extractor.engines)
    extractors = [build_extractor(n, cfg.extractor) for n in names]
    files = [p for p in directory.rglob("*") if p.is_file()]
    for result in run_bakeoff(files, extractors, out_dir=out):
        typer.echo(result.file)
        for r in result.runs:
            status = "ok" if r.ok else f"FAIL: {r.error}"
            typer.echo(f"  {r.engine:<14} {status:<24} {r.chars:>7} chars  {r.table_count} tables  {r.elapsed_s}s")


if __name__ == "__main__":
    app()
