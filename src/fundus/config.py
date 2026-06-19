"""Configuration model and loader.

Loaded from a TOML file (default ``$XDG_CONFIG_HOME/fundus.toml`` or
``~/.config/fundus.toml``). Secrets follow a value-with-env-fallback convention:
a literal value in config, otherwise a named environment variable.

Nothing here is hard-coded to a particular machine — all source locations come
from the user's config.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MeiliConfig(BaseModel):
    url: str = "http://127.0.0.1:7700"
    index: str = "corpus"
    api_key: str | None = None  # falls back to env FUNDUS_MEILI_KEY


class EmbedderConfig(BaseModel):
    # A bare-metal, OpenAI-compatible embeddings endpoint.
    url: str = "http://127.0.0.1:8081/v1/embeddings"
    model: str = "qwen3-embedding"
    dimensions: int | None = None


class EngineConfig(BaseModel):
    url: str
    version: str = "unknown"  # pin to invalidate the extraction cache on engine upgrade


class ExtractorConfig(BaseModel):
    default: str = "docling-serve"
    engines: dict[str, EngineConfig] = Field(default_factory=dict)


class SourceConfig(BaseModel):
    # Connector-specific keys (db, roots, query, ...) are accepted and validated
    # by each connector.
    model_config = ConfigDict(extra="allow")

    name: str
    type: str


class FundusConfig(BaseModel):
    meilisearch: MeiliConfig = Field(default_factory=MeiliConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    # Corpus locales: used for localizedAttributes and as default OCR languages.
    locales: list[str] = Field(default_factory=lambda: ["eng"])
    sources: list[SourceConfig] = Field(default_factory=list)


# --- Paths (XDG) ---------------------------------------------------------------


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fundus.toml"


def _state_home() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "fundus"


def default_state_path() -> Path:
    return _state_home() / "cursors.json"


def default_lock_path() -> Path:
    return _state_home() / "fundus.lock"


def default_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "fundus" / "extractions.db"


def load_config(path: str | Path | None = None) -> FundusConfig:
    target = Path(path) if path else default_config_path()
    data = tomllib.loads(target.read_text()) if target.exists() else {}
    cfg = FundusConfig.model_validate(data)
    if cfg.meilisearch.api_key is None:
        cfg.meilisearch.api_key = os.environ.get("FUNDUS_MEILI_KEY")
    return cfg
