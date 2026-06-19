"""Configuration model and loader.

Loaded from a TOML file (default: ``$XDG_CONFIG_HOME/fundus.toml`` or
``~/.config/fundus.toml``) with environment-variable overrides. Secrets follow a
value-with-env-fallback convention: a literal value in config, otherwise a named
environment variable.

Nothing here is hard-coded to a particular machine — all source locations are
supplied by the user's config.
"""

from __future__ import annotations

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
    # Connector-specific keys (db, roots, query, window, ...) are accepted and
    # validated by each connector.
    model_config = ConfigDict(extra="allow")

    name: str
    type: str


class FundusConfig(BaseModel):
    meilisearch: MeiliConfig = Field(default_factory=MeiliConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
