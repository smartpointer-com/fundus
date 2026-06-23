"""Static bearer-token gate for the MCP HTTP server.

A pure-ASGI wrapper (not Starlette ``BaseHTTPMiddleware``, which buffers and would break SSE
streaming). It checks the ``Authorization: Bearer <token>`` header at request start and 401s
anything that doesn't match — so no random local process can reach the read-only server, even
though it binds to loopback.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

ASGIApp = Callable[[dict[str, Any], Any, Any], Awaitable[None]]


def bearer_guard(app: ASGIApp, token: str) -> ASGIApp:
    """Wrap an ASGI app so every HTTP request must carry ``Authorization: Bearer <token>``."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":  # leave lifespan/websocket scopes untouched
            headers = dict(scope.get("headers") or [])
            if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
                await send(
                    {"type": "http.response.start", "status": 401,
                     "headers": [(b"content-type", b"text/plain")]}
                )
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await app(scope, receive, send)

    return guarded
