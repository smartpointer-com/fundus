"""Structure-aware, size-bounded chunker for documents and email.

Consumes ``ExtractionResult.blocks`` (engine-agnostic), packs them into chunks up
to a token budget with overlap, keeps tables atomic, splits oversized text, and
carries the active heading path as context.
"""

from __future__ import annotations

from fundus.chunk.base import ChunkParams
from fundus.chunk.tokens import count_tokens
from fundus.core.ids import parent_id
from fundus.models import Block, Chunk, ExtractionResult, SourceItem


def _update_path(path: list[str], block: Block) -> list[str]:
    level = max(block.level or 1, 1)
    trimmed = path[: level - 1]
    trimmed = trimmed + [""] * (level - 1 - len(trimmed))
    return [*trimmed, block.text]


def _split_text(text: str, max_tokens: int) -> list[str]:
    """Split text that exceeds the budget, on paragraph then word boundaries."""
    if count_tokens(text) <= max_tokens:
        return [text]
    pieces: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        candidate = f"{buf}\n\n{para}".strip() if buf else para
        if count_tokens(candidate) <= max_tokens:
            buf = candidate
        else:
            if buf:
                pieces.append(buf)
            buf = para
    if buf:
        pieces.append(buf)
    wrapped: list[str] = []
    limit = max_tokens * 4
    for piece in pieces:
        if count_tokens(piece) <= max_tokens:
            wrapped.append(piece)
            continue
        cur: list[str] = []
        for word in piece.split():
            cur.append(word)
            if len(" ".join(cur)) >= limit:
                wrapped.append(" ".join(cur))
                cur = []
        if cur:
            wrapped.append(" ".join(cur))
    return wrapped or [text]


class _Seg:
    __slots__ = ("text", "hpath", "page", "tokens", "is_heading")

    def __init__(
        self, text: str, hpath: list[str], page: int | None, is_heading: bool = False
    ) -> None:
        self.text = text
        self.hpath = hpath
        self.page = page
        self.tokens = count_tokens(text)
        self.is_heading = is_heading


class TextChunker:
    def __init__(self, params: ChunkParams | None = None) -> None:
        self.p = params or ChunkParams()

    def _segments(self, blocks: list[Block]) -> list[_Seg]:
        hpath: list[str] = []
        segs: list[_Seg] = []
        for block in blocks:
            if block.type == "heading":
                hpath = _update_path(hpath, block)
                if block.text.strip():
                    segs.append(_Seg(block.text, list(hpath), block.page, is_heading=True))
                continue
            if not block.text.strip():
                continue
            if block.type == "table":
                segs.append(_Seg(block.text, list(hpath), block.page))  # atomic
            else:
                for piece in _split_text(block.text, self.p.max_tokens):
                    segs.append(_Seg(piece, list(hpath), block.page))
        return segs

    def chunk(self, doc: ExtractionResult, item: SourceItem) -> list[Chunk]:
        pid = parent_id(item.source, item.native_id)
        p = self.p
        chunks: list[Chunk] = []
        cur: list[_Seg] = []
        cur_tokens = 0
        seq = 0
        pending_new = False

        def flush() -> None:
            nonlocal cur, cur_tokens, seq, pending_new
            if pending_new:
                content = [s for s in cur if s.text.strip()]
                if content:
                    body = [s for s in content if not s.is_heading]
                    anchor = body[0] if body else content[0]
                    text = "\n\n".join(s.text for s in content)
                    chunks.append(
                        Chunk(
                            parent_id=pid,
                            seq=seq,
                            text=text,
                            heading_path=anchor.hpath,
                        )
                    )
                    seq += 1
            keep: list[_Seg] = []
            ktok = 0
            for s in reversed(cur):
                if ktok + s.tokens > p.overlap_tokens:
                    break
                keep.append(s)
                ktok += s.tokens
            cur = list(reversed(keep))
            cur_tokens = ktok
            pending_new = False

        for seg in self._segments(doc.blocks):
            if cur and cur_tokens + seg.tokens > p.max_tokens:
                flush()
            cur.append(seg)
            cur_tokens += seg.tokens
            pending_new = True
            if cur_tokens >= p.target_tokens:
                flush()
        if pending_new:
            flush()
        return chunks
