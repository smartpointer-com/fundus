"""Shared rendering for grouped search results (used by `fundus query` and `fundus-client`)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import typer


def fmt_ts(ts: Any) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(ts)


def print_result(n: int, g: dict[str, Any]) -> None:
    """Render one parent-grouped hit: rank, score, source/kind, title, ts, ref, snippet."""
    score = g.get("score")
    score_s = f"{score:.3f}" if isinstance(score, (int, float)) else "  -  "
    ref = g.get("ref") or g.get("parent_id") or ""
    typer.echo(f"{n:2}. {score_s}  [{g.get('source') or '?'}/{g.get('item_kind') or ''}]  "
               f"{g.get('title') or '(untitled)'}")
    typer.echo(f"      {fmt_ts(g.get('ts'))}   ref: {ref}   ({len(g.get('chunks', []))} hit(s))")
    snippet = " ".join((g.get("snippet") or "").split())
    if snippet:
        typer.echo(f"      {snippet}")
