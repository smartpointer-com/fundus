"""Build extraction-engine adapters from configuration."""

from __future__ import annotations

from typing import Callable

from fundus.config import EngineConfig, ExtractorConfig
from fundus.extract.base import Extractor
from fundus.extract.docling import DoclingServeExtractor
from fundus.extract.tika import TikaExtractor

_BUILDERS: dict[str, Callable[[EngineConfig], Extractor]] = {
    "docling-serve": lambda c: DoclingServeExtractor(c.url, version=c.version),
    "tika": lambda c: TikaExtractor(c.url, version=c.version),
}


def available_engines() -> list[str]:
    return sorted(_BUILDERS)


def build_extractor(name: str, config: ExtractorConfig) -> Extractor:
    if name not in _BUILDERS:
        raise KeyError(f"unknown extraction engine: {name!r} (have {available_engines()})")
    if name not in config.engines:
        raise KeyError(f"engine {name!r} is not configured under [extractor.engines]")
    return _BUILDERS[name](config.engines[name])
