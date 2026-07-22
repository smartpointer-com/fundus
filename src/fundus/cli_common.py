"""Options shared by the fundus, fundus-client, and fundus service CLIs."""

from __future__ import annotations

import typer

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to fundus.toml (default: XDG).")
