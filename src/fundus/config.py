"""Configuration model and loader.

Loaded from a TOML file (default ``$XDG_CONFIG_HOME/fundus.toml`` or
``~/.config/fundus.toml``). Secrets follow a value-with-env-fallback convention:
a literal value in config, otherwise a named environment variable. An optional
``env_file`` is sourced at startup so those variables can live in one secrets file
rather than the shell environment.

Nothing here is hard-coded to a particular machine — all source locations come
from the user's config.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MeiliConfig(BaseModel):
    url: str = "http://127.0.0.1:7700"
    index: str = "corpus"
    api_key: str | None = None  # admin/write key; falls back to env FUNDUS_MEILI_KEY
    # Optional read-only, search-scoped key (env FUNDUS_MEILI_SEARCH_KEY). The MCP server prefers it
    # but falls back to the admin key; worth setting only once the server is split out to a
    # less-trusted process (while monolithic it already holds the admin key).
    search_key: str | None = None


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
    # Instruct prefix prepended to QUERIES only (not documents). Set it for asymmetric models like
    # Qwen3-Embedding; leave empty for symmetric ones. When set, Fundus embeds the query itself and
    # passes the vector to Meili (so the prefix never pollutes the keyword query); fan-out indexing
    # relies on this, since a userProvided embedder cannot embed the query server-side.
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
    # OCR engine docling-serve should use, sent as a per-request field (docling-serve's own default
    # is "auto"). Set to "ocrmac" to use Apple Vision on a bare-metal macOS docling-serve — the
    # containerized engine can't reach Vision, so it stays on its CPU default (EasyOCR). None means
    # the field is not sent, preserving docling-serve's default; this is the portable default.
    ocr_engine: str | None = None
    # --- On-demand lifecycle (optional). With `start` set, the engine is raised lazily — on the
    # first extraction request that needs it — and stopped when the run ends, so it consumes
    # memory only while extraction happens. An engine already reachable when first needed is used
    # as-is and never stopped (it wasn't started here). Unset = the engine is expected to be
    # running, exactly as before.
    # `start` alone spawns a managed child process (its output goes to engine-<name>.log under the
    # logs dir); `start` plus `stop` delegates both to commands run to completion (e.g.
    # `docker compose start/stop <service>`).
    start: list[str] = Field(default_factory=list)
    stop: list[str] = Field(default_factory=list)
    # Extra environment merged over the parent's for `start`/`stop` (e.g. a venv on PATH).
    start_env: dict[str, str] = Field(default_factory=dict)
    # Seconds to wait for the engine to answer HTTP after starting (and per delegated command).
    start_timeout: float = 60.0

    @model_validator(mode="after")
    def _stop_requires_start(self) -> "EngineConfig":
        if self.stop and not self.start:
            raise ValueError("engine `stop` is only meaningful together with `start`")
        return self


class RouterConfig(BaseModel):
    # Used by the "escalate" engine: try `fast` first, fall back to `quality` when the fast result
    # has fewer than `min_chars` characters (typically a scanned PDF that needs OCR + layout).
    fast: str = "tika"
    quality: str = "docling-serve"
    min_chars: int = 100
    # Never escalate files larger than this (bytes; None = no cap). A sparse result on a very
    # large file is typically a whole scanned book, and OCRing it can crash the quality engine.
    max_bytes: int | None = None


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


class DoclingServiceConfig(BaseModel):
    # Optional launchd job that keeps a bare-metal docling-serve alive. On Apple Silicon the
    # containerized engine reaches neither Apple Vision (ocrmac) nor the GPU, so docling-serve runs
    # on the host and [extractor.engines.docling-serve].url points at it. It is managed alongside the
    # other `fundus service` jobs (one install/uninstall covers everything) and stays off unless
    # enabled. The launch command and its environment live in config, out of this generic repo.
    # Alternative: an on-demand `start` on the engine itself (EngineConfig) runs docling-serve only
    # while indexing extracts, freeing its memory in between — then this job is unnecessary.
    enabled: bool = False
    # e.g. ["/path/to/venv/bin/docling-serve", "run", "--host", "127.0.0.1", "--port", "5001"]
    command: list[str] = Field(default_factory=list)
    # Extra environment for the server process, merged over the job's HOME/PATH (e.g. a venv bin dir
    # on PATH, a model-cache dir). Unlike fundus's own secrets (which stay in env_file), this IS
    # written into the world-readable plist — so it must not contain secrets.
    environment: dict[str, str] = Field(default_factory=dict)


class ServiceConfig(BaseModel):
    # Defaults for `fundus service` (launchd jobs). Each is overridable by an install-time flag.
    # label_prefix yields the job labels <prefix>.index / .index-full / .serve / .docling (opt-in);
    # use your own reverse-DNS convention (e.g. com.you.fundus). Secrets come from the top-level
    # `env_file`, which every fundus process sources at startup — so they never live in the plist.
    label_prefix: str = "fundus"
    interval_minutes: int = 30  # incremental cadence
    full_at: str = "03:00"  # nightly full-reconcile wall-clock time (HH:MM)
    # Optional keep-alive job for a bare-metal docling-serve (see DoclingServiceConfig); opt-in.
    docling: DoclingServiceConfig = Field(default_factory=DoclingServiceConfig)


class ServeConfig(BaseModel):
    # The read-only MCP server (`fundus serve`) and the thin client that reaches it.
    host: str = "127.0.0.1"
    port: int = 8181
    # streamable-http is the durable MCP transport (HTTP+SSE was deprecated in the 2025-03 spec);
    # "sse" stays available for legacy clients, "stdio" for a host-spawned local subprocess.
    transport: str = "streamable-http"
    # Bearer token clients must present (env FUNDUS_SERVE_TOKEN). Required for the HTTP transports so
    # not just any local process can reach the server; this is the key shared with MCP clients.
    token: str | None = None
    url: str | None = None  # client-side override: full endpoint URL of a running server

    def endpoint(self) -> str:
        """The URL a client uses to reach the server (derived from host/port/transport if unset)."""
        if self.url:
            return self.url
        path = "/sse" if self.transport == "sse" else "/mcp"
        return f"http://{self.host}:{self.port}{path}"


class StorageConfig(BaseModel):
    # Single root for ALL Fundus runtime data: the search index, the SQLite caches, the per-source
    # cursors, and the run lock — each in its own subdirectory. Unset → $XDG_DATA_HOME/fundus
    # (~/.local/share/fundus). The Meilisearch index lives at <data_dir>/meili and is a Docker
    # volume, so the stack mounts it separately (`make up` reads `fundus paths --meili-data`).
    data_dir: str | None = None


class FundusConfig(BaseModel):
    meilisearch: MeiliConfig = Field(default_factory=MeiliConfig)
    embedder: EmbedderConfig = Field(default_factory=EmbedderConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    serve: ServeConfig = Field(default_factory=ServeConfig)
    # Corpus locales: used for localizedAttributes and as default OCR languages.
    locales: list[str] = Field(default_factory=lambda: ["eng"])
    # Indexing concurrency: a thread pool issues extraction requests in parallel so the
    # services (docling/tika, then Meili -> embedder) keep the host cores busy. Tune to the
    # extraction service's capacity and container memory.
    workers: int = Field(default_factory=lambda: min(8, os.cpu_count() or 4))
    # A KEY=VALUE secrets file (e.g. "~/.config/fundus.env") that every fundus process sources at
    # startup if it exists — so FUNDUS_MEILI_KEY / FUNDUS_SERVE_TOKEN / FUNDUS_EMBED_KEY can live in
    # one file instead of the shell. Default unset (no built-in path); override with $FUNDUS_ENV_FILE.
    env_file: str | None = None
    sources: list[SourceConfig] = Field(default_factory=list)

    # --- Resolved data locations (all under one root; see StorageConfig) ---

    def data_root(self) -> Path:
        d = self.storage.data_dir
        return Path(d).expanduser() if d else _data_home()

    def meili_dir(self) -> Path:
        """Where the Meilisearch index should live (mounted into the container as a Docker volume)."""
        return self.data_root() / "meili"

    def extraction_cache_path(self) -> Path:
        return self.data_root() / "cache" / "extractions.db"

    def embed_cache_path(self) -> Path:
        return self.data_root() / "cache" / "embeddings.db"

    def cursors_path(self) -> Path:
        return self.data_root() / "state" / "cursors.json"

    def lock_path(self) -> Path:
        return self.data_root() / "state" / "fundus.lock"

    def logs_dir(self) -> Path:
        """Where the launchd index jobs write their stdout/stderr logs."""
        return self.data_root() / "logs"


# --- Paths ---------------------------------------------------------------------
# Config follows XDG_CONFIG_HOME; all runtime DATA is consolidated under one root
# (see FundusConfig.data_root and StorageConfig), defaulting to XDG_DATA_HOME.


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "fundus.toml"


def _data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "fundus"


def _bash_env(prelude: str) -> dict[str, str]:
    out = subprocess.run(
        ["bash", "-c", f"{prelude}env -0"], check=True, capture_output=True, text=True
    ).stdout
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


def source_env_file(path: Path) -> None:
    """Source a KEY=VALUE secrets file into ``os.environ`` via bash, so quoting/``export`` work and
    we never reimplement env parsing. Diffs the environment before/after sourcing and applies the
    file's contributions (ignoring bash-internal vars); a missing or malformed file is skipped."""
    if not path.is_file():
        return
    try:
        subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)  # syntax check
        before = _bash_env("")
        after = _bash_env(f"set -a; . {shlex.quote(str(path))}; set +a; ")
    except (subprocess.CalledProcessError, OSError, ValueError):
        return
    ignore = {"_", "SHLVL", "PWD", "OLDPWD"}  # bash sets these itself, not from the file
    for key, value in after.items():
        if key not in ignore and before.get(key) != value:
            os.environ[key] = value


def load_config(path: str | Path | None = None) -> FundusConfig:
    target = Path(path) if path else default_config_path()
    data = tomllib.loads(target.read_text()) if target.exists() else {}
    cfg = FundusConfig.model_validate(data)
    # Source the secrets file (if any) BEFORE the env fallbacks below read those variables.
    env_file = os.environ.get("FUNDUS_ENV_FILE") or cfg.env_file
    if env_file:
        source_env_file(Path(env_file).expanduser())
    if cfg.meilisearch.api_key is None:
        cfg.meilisearch.api_key = os.environ.get("FUNDUS_MEILI_KEY")
    if cfg.meilisearch.search_key is None:
        cfg.meilisearch.search_key = os.environ.get("FUNDUS_MEILI_SEARCH_KEY")
    if cfg.embedder.api_key is None:
        cfg.embedder.api_key = os.environ.get("FUNDUS_EMBED_KEY")
    if cfg.serve.token is None:
        cfg.serve.token = os.environ.get("FUNDUS_SERVE_TOKEN")
    return cfg
