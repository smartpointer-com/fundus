"""The indexing pipeline: source -> (extract) -> chunk -> sink.

Routes each item to the right extraction path: text payloads and text/* files pass
through directly, tabular files go straight to the tabular chunker, and binary
documents go through the configured extraction engine (cached). Everything is then
chunked and upserted to the sink. Cursors advance per source; ``full`` runs also
reconcile deletions.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import structlog

from fundus.chunk.dispatch import chunker_for, is_tabular
from fundus.config import (
    FundusConfig,
    default_cache_path,
    default_embed_cache_path,
    default_state_path,
)
from fundus.core.parallel import parallel_map
from fundus.core.reconcile import live_parent_ids
from fundus.core.state import JsonStateStore, StateStore
from fundus.embed.cache import SqliteEmbeddingCache, embed_input_text
from fundus.embed.client import EmbeddingClient
from fundus.embed.config import rest_embedder, user_provided_embedder
from fundus.extract.base import ExtractOptions, ExtractRequest, Extractor
from fundus.extract.cache import ExtractionCache, SqliteExtractionCache, extract_with_cache
from fundus.extract.normalize import markdown_to_blocks
from fundus.extract.registry import build_extractor
from fundus.index.base import IndexSettings, Sink
from fundus.index.mapping import to_index_document
from fundus.index.meili import MeiliSink
from fundus.models import (
    DocMeta,
    EngineRef,
    ExtractionResult,
    IndexDocument,
    SourceItem,
    TextPayload,
)
from fundus.sources.base import Source
from fundus.sources.registry import build_source

log = structlog.get_logger("fundus.pipeline")


def _blob_bytes(payload: object) -> bytes:
    data = getattr(payload, "data", None)
    if isinstance(data, bytes):
        return data
    path = getattr(payload, "path", None)
    if isinstance(path, str):
        return Path(path).read_bytes()
    return b""


def to_extraction(
    item: SourceItem, extractor: Extractor, cache: ExtractionCache, options: ExtractOptions
) -> ExtractionResult:
    """Produce an engine-agnostic ExtractionResult for an item.

    Text payloads and text/* files never touch the extraction engine; only binary
    documents do (cached). Tabular files return an empty placeholder — the tabular
    chunker reads their bytes directly.
    """
    if isinstance(item.payload, TextPayload):
        return ExtractionResult.from_text(item.payload.text)

    mime = item.mime_type or ""
    if is_tabular(mime):
        return ExtractionResult.from_text("")

    data = _blob_bytes(item.payload)
    if mime == "text/markdown":
        text = data.decode("utf-8", errors="replace")
        return ExtractionResult(
            engine=EngineRef(name="native", version="1"),
            blocks=markdown_to_blocks(text),
            markdown=text,
            metadata=DocMeta(),
        )
    if mime == "text/html":
        from selectolax.parser import HTMLParser

        return ExtractionResult.from_text(HTMLParser(data.decode("utf-8", errors="replace")).text())
    if mime.startswith("text/"):
        return ExtractionResult.from_text(data.decode("utf-8", errors="replace"))

    req = ExtractRequest(data=data, mime_type=mime, filename=item.title, options=options)
    return extract_with_cache(extractor, cache, req)


class Pipeline:
    def __init__(
        self,
        config: FundusConfig,
        sink: Sink,
        extractor: Extractor,
        cache: ExtractionCache,
        state: StateStore,
        *,
        batch_size: int = 1000,
        ocr_languages: list[str] | None = None,
        workers: int = 1,
        max_attempts: int = 2,
        retry_backoff: float = 1.0,
        doc_embedder: EmbeddingClient | None = None,
        embed_cache: SqliteEmbeddingCache | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._extractor = extractor
        self._cache = cache
        self._state = state
        self._batch = batch_size
        self._options = ExtractOptions(ocr_languages=ocr_languages or list(config.locales))
        self._workers = max(1, workers)
        self._max_attempts = max(1, max_attempts)
        self._retry_backoff = retry_backoff
        self._doc_embedder = doc_embedder
        self._embed_cache = embed_cache

    def index_settings(self) -> IndexSettings:
        return IndexSettings(locales=list(self._config.locales))

    def ensure_schema(self) -> None:
        self._sink.ensure_schema(self.index_settings())

    def index(self, *, only: str | None = None, full: bool = False, force: bool = False) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cfg in self._config.sources:
            if only and cfg.name != only:
                continue
            counts[cfg.name] = self.index_source(build_source(cfg), full=full, force=force)
        return counts

    def _embed(self, docs: list[IndexDocument]) -> None:
        """Fan-out: compute each doc's vector via the embedder, cache-first. Runs inside the worker
        pool, so N workers issue N concurrent embedding requests. A cache hit skips the call, which
        makes re-running an already-embedded corpus nearly free (only new/changed text is sent)."""
        if self._doc_embedder is None or not docs:
            return
        texts = [embed_input_text(d.title, d.body) for d in docs]
        vectors: list[list[float] | None] = (
            self._embed_cache.get_many(texts) if self._embed_cache else [None] * len(texts)
        )
        missing = [i for i, vec in enumerate(vectors) if vec is None]
        if missing:
            fresh = self._doc_embedder.embed([texts[i] for i in missing])
            for j, i in enumerate(missing):
                vectors[i] = fresh[j]
            if self._embed_cache:
                self._embed_cache.put_many([texts[i] for i in missing], fresh)
        for doc, vector in zip(docs, vectors, strict=True):
            doc.vector = vector

    def _process_item(self, item: SourceItem) -> list[IndexDocument]:
        # Extraction can hit transient extractor-service failures (e.g. dropped connections when
        # the engine is briefly saturated). Retry with a short backoff; only skip the item after
        # exhausting attempts so one flaky request can't silently drop a document.
        for attempt in range(1, self._max_attempts + 1):
            try:
                doc = to_extraction(item, self._extractor, self._cache, self._options)
                docs = [to_index_document(item, chunk) for chunk in chunker_for(item).chunk(doc, item)]
                self._embed(docs)
                return docs
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the whole index
                # Retry only transient transport failures (dropped/refused/reset connections when
                # the engine is briefly saturated). A definitive HTTP response (e.g. 504 on a file
                # the engine can't convert in time) or any other error is permanent for this item;
                # retrying just burns the engine on a doomed document and starves the queue.
                transient = isinstance(exc, httpx.TransportError)
                if transient and attempt < self._max_attempts:
                    time.sleep(self._retry_backoff)
                    continue
                log.warning(
                    "item failed",
                    source=item.source,
                    native_id=item.native_id,
                    attempts=attempt,
                    error=str(exc),
                )
                return []
        return []  # unreachable; satisfies the type checker

    def index_source(self, source: Source, *, full: bool = False, force: bool = False) -> int:
        cursor = None if force else self._state.get_cursor(source.name)
        batch: list[IndexDocument] = []
        total = 0
        # Workers extract + chunk concurrently; the main thread batches the upserts.
        for docs in parallel_map(source.changed(cursor), self._process_item, workers=self._workers):
            batch.extend(docs)
            total += len(docs)
            if len(batch) >= self._batch:
                self._sink.upsert(batch)
                batch = []
        if batch:
            self._sink.upsert(batch)
        self._state.set_cursor(source.name, source.current_cursor())
        if full:
            removed = self._sink.delete_missing(source.name, live_parent_ids(source))
            log.info("reconciled", source=source.name, removed=removed)
        log.info("indexed", source=source.name, chunks=total)
        return total


def build_sink(config: FundusConfig) -> MeiliSink:
    embedder_cfg = config.embedder
    query_embedder = None
    if embedder_cfg.query_prompt:  # opt-in: only self-embed queries when a prefix is configured
        query_embedder = EmbeddingClient(
            url=embedder_cfg.query_url or embedder_cfg.url,
            model=embedder_cfg.model,
            api_key=embedder_cfg.api_key,
            query_prompt=embedder_cfg.query_prompt,
        )
    embedders = (
        user_provided_embedder(embedder_cfg)
        if embedder_cfg.fanout
        else rest_embedder(embedder_cfg)
    )
    return MeiliSink(
        url=config.meilisearch.url,
        index=config.meilisearch.index,
        api_key=config.meilisearch.api_key,
        embedders=embedders,
        query_embedder=query_embedder,
    )


def build_pipeline(
    config: FundusConfig,
    *,
    sink: Sink | None = None,
    cache_path: str | None = None,
    state_path: str | None = None,
) -> Pipeline:
    doc_embedder = None
    embed_cache = None
    if config.embedder.fanout:
        # Documents are embedded without the query instruct prefix; use the host-side URL and a
        # generous timeout (a batch of long chunks on a heavy model can take a while).
        doc_embedder = EmbeddingClient(
            url=config.embedder.query_url or config.embedder.url,
            model=config.embedder.model,
            api_key=config.embedder.api_key,
            query_prompt="",
            timeout=180.0,
        )
        embed_cache = SqliteEmbeddingCache(str(default_embed_cache_path()), config.embedder.model)
    return Pipeline(
        config,
        sink or build_sink(config),
        build_extractor(config.extractor.default, config.extractor),
        SqliteExtractionCache(cache_path or str(default_cache_path())),
        JsonStateStore(state_path or str(default_state_path())),
        ocr_languages=list(config.locales),
        workers=config.workers,
        doc_embedder=doc_embedder,
        embed_cache=embed_cache,
    )
