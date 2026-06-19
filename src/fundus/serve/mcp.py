"""MCP server exposing read-only search to agents.

The full server is wired in the serve phase; ``run_server`` is the entry point the
CLI calls.
"""

from __future__ import annotations

from fundus.config import FundusConfig


def run_server(config: FundusConfig) -> None:
    raise NotImplementedError("MCP serve is implemented in the serve phase")
