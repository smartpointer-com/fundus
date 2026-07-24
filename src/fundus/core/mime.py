"""Best-effort MIME-type detection."""

from __future__ import annotations

import mimetypes


def guess_mime(data: bytes, filename: str | None = None) -> str:
    """Guess a MIME type from content (preferred), then filename, else octet-stream."""
    try:
        import puremagic

        guess = puremagic.from_string(data, mime=True)
        if guess:
            return guess
    except Exception:  # noqa: S110 - detection is best-effort; fall through to filename
        pass
    if filename:
        mt, _ = mimetypes.guess_type(filename)
        if mt:
            return mt
    return "application/octet-stream"
