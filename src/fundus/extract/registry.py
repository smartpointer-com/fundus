"""Build extraction-engine adapters from configuration."""

from __future__ import annotations

from typing import Callable

from fundus.config import EngineConfig, ExtractorConfig
from fundus.extract.base import Extractor
from fundus.extract.docling import DoclingServeExtractor
from fundus.extract.router import EscalatingExtractor
from fundus.extract.tika import TikaExtractor

_BUILDERS: dict[str, Callable[[EngineConfig], Extractor]] = {
    "docling-serve": lambda c: DoclingServeExtractor(
        c.url, version=c.version, max_concurrency=c.max_concurrency, timeout=c.timeout
    ),
    "tika": lambda c: TikaExtractor(c.url, version=c.version),
}


def available_engines() -> list[str]:
    return [*sorted(_BUILDERS), "escalate"]


def build_extractor(name: str, config: ExtractorConfig) -> Extractor:
    if name == "escalate":
        rc = config.router
        return EscalatingExtractor(
            build_extractor(rc.fast, config),
            build_extractor(rc.quality, config),
            min_chars=rc.min_chars,
            max_bytes=rc.max_bytes,
        )
    if name not in _BUILDERS:
        raise KeyError(f"unknown extraction engine: {name!r} (have {available_engines()})")
    if name not in config.engines:
        raise KeyError(f"engine {name!r} is not configured under [extractor.engines]")
    return _BUILDERS[name](config.engines[name])
