"""Fundus command-line interface."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

from fundus.bakeoff.runner import run_bakeoff
from fundus.config import default_lock_path, load_config
from fundus.core.locking import AlreadyRunning, run_lock
from fundus.core.pipeline import build_pipeline, build_sink
from fundus.extract.registry import build_extractor

app = typer.Typer(add_completion=False, help="Index and search a heterogeneous corpus.")

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
    full: bool = typer.Option(False, help="Full pass plus deletion reconciliation."),
    force: bool = typer.Option(False, help="Ignore saved cursors and re-read everything."),
    workers: int | None = typer.Option(None, help="Override the indexing worker count."),
    config: Path | None = ConfigOpt,
) -> None:
    """Run the ingestion pipeline (incremental by default)."""
    cfg = load_config(config)
    if workers is not None:
        cfg.workers = workers
    try:
        with run_lock(default_lock_path()):
            counts = build_pipeline(cfg).index(only=only, full=full, force=force)
    except AlreadyRunning:
        typer.echo("Another fundus run holds the lock; skipping.")
        raise typer.Exit(code=0) from None
    for name, n in counts.items():
        typer.echo(f"{name}: {n} chunks")


def _fmt_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts)


def _print_result(n: int, g: dict[str, Any]) -> None:
    score = g.get("score")
    score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "  -  "
    ref = g.get("ref") or g.get("parent_id") or ""
    typer.echo(f"{n:2}. {score_s}  [{g.get('source') or '?'}/{g.get('item_kind') or ''}]  "
               f"{g.get('title') or '(untitled)'}")
    typer.echo(f"      {_fmt_ts(g.get('ts'))}   ref: {ref}   ({len(g.get('chunks', []))} hit(s))")
    snippet = " ".join((g.get("snippet") or "").split())
    if snippet:
        typer.echo(f"      {snippet}")


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
def serve(config: Path | None = ConfigOpt) -> None:
    """Expose read-only search to agents over MCP."""
    from fundus.serve.mcp import run_server

    run_server(load_config(config))


@app.command(name="embed-backfill")
def embed_backfill(config: Path | None = ConfigOpt) -> None:
    """Seed the embedding cache from vectors already in the index, so the next re-index reuses
    them instead of recomputing. One-time bootstrap; the cache self-populates thereafter."""
    from fundus.config import default_embed_cache_path
    from fundus.embed.backfill import backfill_embedding_cache
    from fundus.embed.cache import SqliteEmbeddingCache

    cfg = load_config(config)
    cache = SqliteEmbeddingCache(str(default_embed_cache_path()), cfg.embedder.model)
    written = backfill_embedding_cache(
        url=cfg.meilisearch.url,
        index=cfg.meilisearch.index,
        api_key=cfg.meilisearch.api_key,
        cache=cache,
    )
    typer.echo(f"Backfilled {written:,} vectors into the embedding cache.")


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
