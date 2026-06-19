"""Fundus command-line interface."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="Index and search a heterogeneous corpus.")


def _todo(name: str) -> None:
    typer.echo(f"`{name}` is not implemented yet — see docs/implementation-plan.md")
    raise typer.Exit(code=1)


@app.command()
def init() -> None:
    """Write a starter config and ensure the index schema exists."""
    _todo("init")


@app.command()
def index(
    only: str | None = typer.Option(None, help="Limit to a single source by name."),
    full: bool = typer.Option(False, help="Full pass plus deletion reconciliation."),
    force: bool = typer.Option(False, help="Ignore saved cursors and re-read everything."),
) -> None:
    """Run the ingestion pipeline (incremental by default)."""
    _todo("index")


@app.command()
def query(text: str) -> None:
    """Hybrid search from the command line."""
    _todo("query")


@app.command()
def serve() -> None:
    """Expose read-only search to agents over MCP."""
    _todo("serve")


@app.command(name="sources")
def list_sources() -> None:
    """List configured sources."""
    _todo("sources")


@app.command()
def bakeoff() -> None:
    """Run extraction engines over a sample and compare their output."""
    _todo("bakeoff")


if __name__ == "__main__":
    app()
