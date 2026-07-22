"""Normalize raw engine output (Markdown or plain text) into Blocks.

Engine adapters that emit Markdown (docling) call ``markdown_to_blocks``;
adapters that emit only flat text (Tika) call ``text_to_blocks``. Keeping this
here means the chunker sees one structural model regardless of engine.
"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

from fundus.models import Block

_md = MarkdownIt("commonmark").enable("table")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _slice(lines: list[str], token: object) -> str:
    span = getattr(token, "map", None)
    if not span:
        return getattr(token, "content", "").strip()
    start, end = span
    return "\n".join(lines[start:end]).strip()


def markdown_to_blocks(md: str) -> list[Block]:
    """Parse Markdown into ordered, top-level structural blocks."""
    if not md or not md.strip():
        return []
    lines = md.splitlines()
    tokens = _md.parse(md)
    blocks: list[Block] = []
    for idx, tok in enumerate(tokens):
        if tok.level != 0:
            continue  # only top-level blocks; children are handled via their parent
        if tok.type == "heading_open":
            text = tokens[idx + 1].content if idx + 1 < len(tokens) else ""
            blocks.append(Block(type="heading", level=int(tok.tag[1:]), text=text))
        elif tok.type == "paragraph_open":
            text = tokens[idx + 1].content if idx + 1 < len(tokens) else ""
            blocks.append(Block(type="paragraph", text=text))
        elif tok.type == "table_open":
            raw = _slice(lines, tok)
            blocks.append(Block(type="table", text=raw))
        elif tok.type in ("bullet_list_open", "ordered_list_open"):
            blocks.append(Block(type="list", text=_slice(lines, tok)))
        elif tok.type in ("fence", "code_block"):
            blocks.append(Block(type="code", text=tok.content.rstrip("\n")))
        elif tok.type == "blockquote_open":
            blocks.append(Block(type="paragraph", text=_slice(lines, tok)))
    return blocks


def text_to_blocks(text: str) -> list[Block]:
    """Split flat text into paragraph blocks on blank lines."""
    if not text or not text.strip():
        return []
    return [Block(type="paragraph", text=p.strip()) for p in _PARA_SPLIT.split(text) if p.strip()]
