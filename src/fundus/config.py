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
    api_key: str | None = None  # falls back to env FUNDUS_EMBED_KEY; sent as a bearer token
    # URL Fundus itself uses to embed QUERIES (host-side); falls back to `url`. Needed when Meili
    # reaches the embedder at a different address than the host process does (e.g. Meili-in-Docker
    # uses host.docker.internal while the host uses 127.0.0.1).
    query_url: str | None = None
    # Instruct prefix prepended to QUERIES only (not documents). Empty = symmetric (Meili embeds
    # the query). Set this for models like Qwen3-Embedding that want an asymmetric query prompt.
    query_prompt: str = ""
    # When true, Fundus computes document vectors itself (concurrently, via the indexing worker
    # pool) and pushes them to Meili through a userProvided embedder, instead of Meili embedding
    # one request at a time over REST. Big win when the model is heavy and Meili's sequential
    # embedding is the bottleneck. Requires `dimensions` to be set.
    fanout: bool = False


class EngineConfig(BaseModel):
    url: str
    version: str = "unknown"  # pin to invalidate the extraction cache on engine upgrade
    # Cap on concurrent requests Fundus sends to this engine (None = unlimited). docling-serve
    # drops queued connections when flooded, so cap it at/below its own worker count, decoupled
    # from the (higher) indexing worker count used to drive embedding concurrency.
    max_concurrency: int | None = None
    # HTTP read timeout (seconds). Keep it above the engine's own job timeout (e.g. docling-serve's
    # max_sync_wait) so slow OCR on large scans finishes instead of the client giving up first.
    timeout: float = 600.0


class RouterConfig(BaseModel):
    # Used by the "escalate" engine: try `fast` first, fall back to `quality` when the fast result
    # has fewer than `min_chars` characters (typically a scanned PDF that needs OCR + layout).
    fast: str = "tika"
    quality: str = "docling-serve"
    min_chars: int = 100


class ExtractorConfig(BaseModel):
    default: str = "docling-serve"
    engines: dict[str, EngineConfig] = Field(default_factory=dict)
    router: RouterConfig = Field(default_factory=RouterConfig)


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
    # Indexing concurrency: a thread pool issues extraction requests in parallel so the
    # services (docling/tika, then Meili -> embedder) keep the host cores busy. Tune to the
    # extraction service's capacity and container memory.
    workers: int = Field(default_factory=lambda: min(8, os.cpu_count() or 4))
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


def default_embed_cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "fundus" / "embeddings.db"


def load_config(path: str | Path | None = None) -> FundusConfig:
    target = Path(path) if path else default_config_path()
    data = tomllib.loads(target.read_text()) if target.exists() else {}
    cfg = FundusConfig.model_validate(data)
    if cfg.meilisearch.api_key is None:
        cfg.meilisearch.api_key = os.environ.get("FUNDUS_MEILI_KEY")
    if cfg.embedder.api_key is None:
        cfg.embedder.api_key = os.environ.get("FUNDUS_EMBED_KEY")
    return cfg
