"""The Chunker interface — turns a normalized ExtractionResult into Chunks.

Chunkers consume ``ExtractionResult.blocks``, never engine-specific output, so a
single chunker family works across all extraction engines. Different item kinds
use different chunkers (prose/document, chat window, tabular), selected in
``chunk/dispatch.py``.
"""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable

from pydantic import BaseModel

from fundus.models import Chunk, ExtractionResult, SourceItem


class ChunkParams(BaseModel):
    target_tokens: int = 512
    max_tokens: int = 1024
    overlap_tokens: int = 64


@runtime_checkable
class Chunker(Protocol):
    def chunk(self, doc: ExtractionResult, item: SourceItem) -> Iterator[Chunk]: ...
