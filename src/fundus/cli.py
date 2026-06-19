"""Fundus command-line interface."""

from __future__ import annotations

from pathlib import Path

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
    config: Path | None = ConfigOpt,
) -> None:
    """Run the ingestion pipeline (incremental by default)."""
    cfg = load_config(config)
    try:
        with run_lock(default_lock_path()):
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
    config: Path | None = ConfigOpt,
) -> None:
    """Hybrid search from the command line."""
    cfg = load_config(config)
    groups = build_sink(cfg).search(text, semantic_ratio=semantic_ratio, filters=filters, limit=limit)
    if not groups:
        typer.echo("(no results)")
    for group in groups:
        typer.echo(f"[{group.get('source')}] {group.get('title')}  ({len(group['chunks'])} hit(s))")


@app.command()
def serve(config: Path | None = ConfigOpt) -> None:
    """Expose read-only search to agents over MCP."""
    from fundus.serve.mcp import run_server

    run_server(load_config(config))


@app.command(name="sources")
def list_sources(config: Path | None = ConfigOpt) -> None:
    """List configured sources."""
    for source in load_config(config).sources:
        typer.echo(f"{source.name}\t{source.type}")


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
