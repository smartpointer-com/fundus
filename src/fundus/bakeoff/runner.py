"""Run extraction engines over a sample set and compare their output.

Supports choosing the default engine empirically on the target corpus before
wiring the rest of the pipeline. A failing engine is recorded, not fatal.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel, Field

from fundus.core.mime import guess_mime
from fundus.extract.base import ExtractOptions, Extractor, ExtractRequest
from fundus.extract.cache import ExtractionCache, extract_with_cache
from fundus.models import ExtractionResult


class EngineRun(BaseModel):
    engine: str
    ok: bool
    error: str | None = None
    elapsed_s: float = 0.0
    chars: int = 0
    table_count: int = 0
    ocr_used: bool = False


class FileBakeoff(BaseModel):
    file: str
    runs: list[EngineRun] = Field(default_factory=list)


def _measure(
    extractor: Extractor, req: ExtractRequest, cache: ExtractionCache | None
) -> tuple[ExtractionResult, EngineRun]:
    start = time.perf_counter()
    result = extract_with_cache(extractor, cache, req) if cache else extractor.extract(req)
    elapsed = time.perf_counter() - start
    run = EngineRun(
        engine=extractor.name,
        ok=True,
        elapsed_s=round(elapsed, 3),
        chars=len(result.markdown),
        table_count=sum(1 for block in result.blocks if block.type == "table"),
        ocr_used=result.metadata.ocr_used,
    )
    return result, run


def run_bakeoff(
    files: list[Path],
    extractors: list[Extractor],
    *,
    cache: ExtractionCache | None = None,
    out_dir: Path | None = None,
    options: ExtractOptions | None = None,
) -> list[FileBakeoff]:
    options = options or ExtractOptions()
    results: list[FileBakeoff] = []
    for raw in files:
        path = Path(raw)
        data = path.read_bytes()
        mime = guess_mime(data, path.name)
        fb = FileBakeoff(file=str(path))
        for extractor in extractors:
            req = ExtractRequest(data=data, mime_type=mime, filename=path.name, options=options)
            try:
                result, run = _measure(extractor, req, cache)
            except Exception as exc:  # one failed engine must not stop the sweep
                fb.runs.append(EngineRun(engine=extractor.name, ok=False, error=str(exc)))
                continue
            fb.runs.append(run)
            if out_dir is not None:
                dest = Path(out_dir) / extractor.name
                dest.mkdir(parents=True, exist_ok=True)
                (dest / f"{path.stem}.md").write_text(result.markdown, encoding="utf-8")
        results.append(fb)
    return results
