"""Shared SQLite connection helper for the on-disk caches."""

from __future__ import annotations

import sqlite3


def connect(path: str, *, timeout: float = 30.0) -> sqlite3.Connection:
    """A fresh connection for one cache operation; callers close deterministically (``closing``).

    sqlite3's context manager only scopes the TRANSACTION, and an unclosed connection sits on a
    file descriptor until the cyclic GC runs — a long indexing run exhausts the fd limit long
    before that. The busy timeout lets concurrent writers from the worker pool wait instead of
    erroring "database is locked"; WAL lets them interleave with readers instead of serializing
    on the whole file.
    """
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
