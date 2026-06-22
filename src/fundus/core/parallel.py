"""Bounded parallel map over a stream of items, using a thread pool.

Indexing is dominated by extraction (HTTP calls to docling/tika) and, downstream,
embedding (Meili -> the model server). Those are I/O-bound from the orchestrator's
side, so a thread pool that issues many requests concurrently keeps the services
(and thus the host cores) busy. Bounded in-flight work so a huge source never
buffers in memory; results are yielded as they complete (order not preserved).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def parallel_map(
    items: Iterable[T],
    fn: Callable[[T], R],
    *,
    workers: int,
    max_inflight: int | None = None,
) -> Iterator[R]:
    """Yield ``fn(item)`` for each item, running up to ``workers`` concurrently.

    With ``workers <= 1`` runs sequentially. At most ``max_inflight`` (default
    ``workers * 2``) futures are pending at once.
    """
    if workers <= 1:
        for item in items:
            yield fn(item)
        return

    limit = max_inflight or workers * 2
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending: set[Future[R]] = set()
        for item in items:
            pending.add(executor.submit(fn, item))
            while len(pending) >= limit:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
