"""Per-sheet, row-group chunker for spreadsheets and CSV.

Each chunk repeats the header row so it is self-describing, and records its sheet
and row range in ``Chunk.meta``. Parsing uses ``python-calamine`` (xlsx/xls/ods)
and the stdlib ``csv`` module.
"""

from __future__ import annotations

import csv as csvmod
import io
from pathlib import Path

from fundus.chunk.base import ChunkParams
from fundus.chunk.tokens import count_tokens
from fundus.core.ids import parent_id
from fundus.models import Chunk, ExtractionResult, SourceItem


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def _render(header: list[str] | None, rows: list[list[str]]) -> str:
    lines: list[str] = []
    if header:
        lines.append(" | ".join(header))
        lines.append(" | ".join("---" for _ in header))
    lines.extend(" | ".join(r) for r in rows)
    return "\n".join(lines)


def parse_sheets(data: bytes, mime: str | None) -> list[tuple[str, list[list[str]]]]:
    if mime == "text/csv" or mime is None:
        text = data.decode("utf-8", errors="replace")
        rows = [[_cell(c) for c in r] for r in csvmod.reader(io.StringIO(text))]
        return [("", rows)]
    from python_calamine import CalamineWorkbook

    workbook = CalamineWorkbook.from_filelike(io.BytesIO(data))
    sheets: list[tuple[str, list[list[str]]]] = []
    for name in workbook.sheet_names:
        raw = workbook.get_sheet_by_name(name).to_python()
        sheets.append((name, [[_cell(c) for c in row] for row in raw]))
    return sheets


def _payload_bytes(item: SourceItem) -> bytes:
    payload = item.payload
    data = getattr(payload, "data", None)
    if isinstance(data, bytes):
        return data
    path = getattr(payload, "path", None)
    if isinstance(path, str):
        return Path(path).read_bytes()
    return b""


class TabularChunker:
    def __init__(self, params: ChunkParams | None = None) -> None:
        self.p = params or ChunkParams()

    def chunk_sheet(
        self, sheet: str, rows: list[list[str]], item: SourceItem, start_seq: int = 0
    ) -> list[Chunk]:
        if not rows:
            return []
        pid = parent_id(item.source, item.native_id)
        header = rows[0]
        base_tokens = count_tokens(_render(header, []))
        chunks: list[Chunk] = []
        cur: list[list[str]] = []
        cur_tokens = base_tokens
        seq = start_seq
        group_start = 1
        row_no = 1

        def flush(end_row: int) -> None:
            nonlocal cur, cur_tokens, seq
            if not cur:
                return
            text = _render(header, cur)
            chunks.append(
                Chunk(
                    parent_id=pid,
                    seq=seq,
                    text=text,
                    heading_path=[sheet] if sheet else [],
                    token_count=count_tokens(text),
                    meta={"sheet": sheet, "row_start": group_start, "row_end": end_row},
                )
            )
            seq += 1
            cur = []
            cur_tokens = base_tokens

        for row in rows[1:]:
            rtok = count_tokens(_render(None, [row]))
            if cur and cur_tokens + rtok > self.p.max_tokens:
                flush(row_no - 1)
                group_start = row_no
            cur.append(row)
            cur_tokens += rtok
            row_no += 1
        flush(row_no - 1)
        return chunks

    def chunk(self, doc: ExtractionResult, item: SourceItem) -> list[Chunk]:
        chunks: list[Chunk] = []
        seq = 0
        for name, rows in parse_sheets(_payload_bytes(item), item.mime_type):
            sheet_chunks = self.chunk_sheet(name, rows, item, start_seq=seq)
            chunks.extend(sheet_chunks)
            seq += len(sheet_chunks)
        return chunks
