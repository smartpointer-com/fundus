"""The indexing pipeline: source -> (extract) -> chunk -> sink.

Routes each item to the right extraction path: text payloads and text/* files pass
through directly, tabular files go straight to the tabular chunker, and binary
documents go through the configured extraction engine (cached). Everything is then
chunked and upserted to the sink. Cursors advance per source; ``full`` runs also
reconcile deletions.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import partial

import httpx
import structlog

from fundus.chunk.dispatch import chunker_for, is_tabular
from fundus.config import FundusConfig
from fundus.core.ids import parent_id
from fundus.core.parallel import parallel_map
from fundus.core.reconcile import diff_tree, live_parent_ids
from fundus.core.state import JsonStateStore, StateStore
from fundus.embed.cache import SqliteEmbeddingCache, embed_input_text
from fundus.embed.client import EmbeddingClient
from fundus.embed.config import rest_embedder, user_provided_embedder
from fundus.extract.base import ExtractOptions, Extractor, ExtractRequest
from fundus.extract.cache import ExtractionCache, SqliteExtractionCache, extract_with_cache
from fundus.extract.lifecycle import EngineLifecycles, build_lifecycles, install_sigterm_handler
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
    payload_bytes,
)
from fundus.sources.base import Source, TreeSource
from fundus.sources.registry import build_source

log = structlog.get_logger("fundus.pipeline")

# A --full reconcile aborts rather than prune more than a quarter of a source's indexed files
# (small trees get this absolute allowance instead, where large relative churn is normal).
_MASS_DELETE_FLOOR = 100


def to_extraction(
    item: SourceItem,
    extractor: Extractor,
    cache: ExtractionCache,
    options: ExtractOptions,
    *,
    refresh: bool = False,
) -> ExtractionResult:
    """Produce an engine-agnostic ExtractionResult for an item.

    Text payloads and text/* files never touch the extraction engine; only binary
    documents do (cached; ``refresh`` bypasses the cache read to force a re-extract).
    Tabular files return an empty placeholder — the tabular chunker reads their
    bytes directly.
    """
    if isinstance(item.payload, TextPayload):
        return ExtractionResult.from_text(item.payload.text)

    mime = item.mime_type or ""
    if is_tabular(mime):
        return ExtractionResult.from_text("")

    data = payload_bytes(item.payload)
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
    return extract_with_cache(extractor, cache, req, refresh=refresh)


def engine_fingerprints(extractor: Extractor) -> dict[str, str]:
    """Current fingerprint per engine name that can author results under this extractor.

    The escalating router reports its inner engines (results carry the inner engine's
    name); a plain engine reports itself.
    """
    inner = getattr(extractor, "engine_fingerprints", None)
    if callable(inner):
        return dict(inner())
    return {extractor.name: extractor.fingerprint}


@dataclass
class IndexReport:
    """Outcome of one ``index`` run: chunks written per source, plus any source that failed
    outright. Failures are collected rather than raised so the remaining sources still run;
    the caller is expected to surface them (a silent partial run is how a stuck source goes
    unnoticed for days)."""

    counts: dict[str, int] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)


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
        lifecycles: EngineLifecycles | None = None,
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
        # Lifecycles of on-demand engines (config `start`); runs stop what they started.
        self._lifecycles = lifecycles
        # name -> current fingerprint for every engine that can author a result here;
        # the "expected" side of the extract_sig staleness check.
        self._engine_fps = engine_fingerprints(extractor)

    def _extract_sig(self, extraction: ExtractionResult) -> str:
        """Provenance stamp for an extraction: ``<engine>:<fingerprint>``.

        Prefers the fingerprint recorded in the result (exact even on a cache hit);
        results cached before the field existed fall back to the producing engine's
        current fingerprint — a best effort that self-corrects on the next re-parse.
        """
        name = extraction.engine.name
        fp = extraction.engine_fingerprint
        if fp is None:
            fp = self._engine_fps.get(name, "")
        return f"{name}:{fp}"

    def _sig_stale(self, sig: str) -> bool:
        """Whether an indexed document's stored extract_sig no longer matches the engine
        that would produce it today. Unknown engines and pre-feature docs (empty sig)
        are never stale — they converge when the document naturally re-indexes, or via
        ``fundus reparse``."""
        if not sig or ":" not in sig:
            return False
        name, fp = sig.split(":", 1)
        expected = self._engine_fps.get(name)
        return expected is not None and fp != expected

    def index_settings(self) -> IndexSettings:
        return IndexSettings(locales=list(self._config.locales))

    def ensure_schema(self) -> None:
        self._sink.ensure_schema(self.index_settings())

    def index(
        self,
        *,
        only: str | None = None,
        full: bool = False,
        force: bool = False,
        allow_mass_delete: bool = False,
    ) -> IndexReport:
        try:
            return self._index(
                only=only, full=full, force=force, allow_mass_delete=allow_mass_delete
            )
        finally:
            if self._lifecycles:
                self._lifecycles.shutdown()

    def _index(
        self,
        *,
        only: str | None,
        full: bool,
        force: bool,
        allow_mass_delete: bool,
    ) -> IndexReport:
        report = IndexReport()
        for cfg in self._config.sources:
            if only and cfg.name != only:
                continue
            # One broken source must not abort the whole index — same rule as _process_item
            # applies one level up. Without this, a source that fails before yielding its
            # first item silently freezes the cursor of every source configured below it.
            try:
                report.counts[cfg.name] = self.index_source(
                    build_source(cfg), full=full, force=force, allow_mass_delete=allow_mass_delete
                )
            except Exception as exc:  # one bad source must not abort the rest
                log.error("source failed", source=cfg.name, error=str(exc))
                report.failures[cfg.name] = str(exc)
        return report

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

    def _process_item(
        self, item: SourceItem, refresh_nids: set[str] | None = None
    ) -> list[IndexDocument]:
        # Extraction can hit transient extractor-service failures (e.g. dropped connections when
        # the engine is briefly saturated). Retry with a short backoff; only skip the item after
        # exhausting attempts so one flaky request can't silently drop a document.
        refresh = refresh_nids is not None and item.native_id in refresh_nids
        for attempt in range(1, self._max_attempts + 1):
            try:
                doc = to_extraction(
                    item, self._extractor, self._cache, self._options, refresh=refresh
                )
                sig = self._extract_sig(doc)
                docs = [
                    to_index_document(
                        item,
                        chunk,
                        extract_sig=sig,
                        ocr_used=doc.metadata.ocr_used,
                        languages=doc.metadata.languages,
                    )
                    for chunk in chunker_for(item).chunk(doc, item)
                ]
                self._embed(docs)
                return docs
            except Exception as exc:  # one bad item must not abort the whole index
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

    def _drain(self, items: Iterable[SourceItem], refresh_nids: set[str] | None = None) -> int:
        """Process items through the worker pool, batching upserts; return chunks written.

        Items whose native_id is in ``refresh_nids`` re-extract through the cache-bypassing
        refresh path (their indexed text was produced under superseded extraction settings)."""
        batch: list[IndexDocument] = []
        total = 0
        # Workers extract + chunk concurrently; the main thread batches the upserts.
        process = partial(self._process_item, refresh_nids=refresh_nids)
        for docs in parallel_map(items, process, workers=self._workers):
            batch.extend(docs)
            total += len(docs)
            if len(batch) >= self._batch:
                self._sink.upsert(batch)
                batch = []
        if batch:
            self._sink.upsert(batch)
        return total

    def index_source(
        self,
        source: Source,
        *,
        full: bool = False,
        force: bool = False,
        allow_mass_delete: bool = False,
    ) -> int:
        # A full pass on a file tree reconciles by content fingerprint, not the mtime cursor.
        if full and isinstance(source, TreeSource):
            return self._reconcile_tree(source, allow_mass_delete=allow_mass_delete)
        cursor = None if force else self._state.get_cursor(source.name)
        total = self._drain(source.changed(cursor))
        self._state.set_cursor(source.name, source.current_cursor())
        if full:
            removed = self._sink.delete_missing(source.name, live_parent_ids(source))
            log.info("reconciled", source=source.name, removed=removed)
        log.info("indexed", source=source.name, chunks=total)
        return total

    def _reconcile_tree(self, source: TreeSource, *, allow_mass_delete: bool = False) -> int:
        """Rsync-style ``--full`` for a file tree: diff the live tree's fingerprints against the
        indexed manifest, re-index new/edited files and drop removed ones — independent of the
        mtime cursor, so an edit is caught even if its mtime regressed or never moved. Unedited
        files whose stored extract_sig no longer matches the current extraction configuration
        (e.g. a different OCR engine) re-parse through the cache-bypassing refresh path."""
        disk = dict(source.fingerprints())
        manifest = self._sink.indexed_manifest(source.name)
        changed, removed = diff_tree(disk, {nid: e.stat for nid, e in manifest.items()})
        stale = {
            nid
            for nid, entry in manifest.items()
            if nid in disk and nid not in changed and self._sig_stale(entry.extract_sig)
        }
        if stale:
            log.info(
                "extraction settings changed; re-parsing", source=source.name, stale=len(stale)
            )
        # Guardrail against mass pruning: when a large share of the manifest reads as removed,
        # an incomplete enumeration (unmounted volume, permission failure, resource exhaustion)
        # is as likely as a genuine mass deletion — and pruning on a bad read is destructive.
        # Abort by default; an intentional purge re-runs with --allow-mass-delete.
        if not allow_mass_delete and len(removed) > max(_MASS_DELETE_FLOOR, len(manifest) // 4):
            raise RuntimeError(
                f"refusing to prune {len(removed)} of {len(manifest)} indexed files for source "
                f"{source.name!r}: either the live tree is incompletely enumerated, or this is "
                "a deliberate mass deletion — re-run with --allow-mass-delete if it is"
            )
        # Clear changed parents too (not just removed): a file that re-chunks into fewer pieces
        # would otherwise leave orphaned high-seq chunks behind under the same parent id.
        dead = {parent_id(source.name, nid) for nid in changed | stale | removed}
        self._sink.delete_parents(dead)
        items = (item for nid in changed | stale if (item := source.load(nid)) is not None)
        total = self._drain(items, refresh_nids=stale)
        self._state.set_cursor(source.name, source.current_cursor())
        log.info(
            "reconciled",
            source=source.name,
            reindexed=len(changed),
            reparsed=len(stale),
            removed=len(removed),
            chunks=total,
        )
        return total

    def reparse(self, source: TreeSource, native_ids: Iterable[str]) -> dict[str, int]:
        """Force-re-extract selected indexed files, bypassing the extraction cache.

        For engine upgrades (the cache key deliberately ignores live engine versions) or
        any other suspicion of stale text. Unlike ``_reconcile_tree`` this deletes a
        document's old chunks only AFTER its fresh extraction succeeded, so an engine
        outage mid-run cannot wipe serviceable documents from the index.
        """
        try:
            return self._reparse(source, native_ids)
        finally:
            if self._lifecycles:
                self._lifecycles.shutdown()

    def _reparse(self, source: TreeSource, native_ids: Iterable[str]) -> dict[str, int]:
        nids = sorted(set(native_ids))
        counts = {"selected": len(nids), "reparsed": 0, "missing": 0, "failed": 0, "chunks": 0}

        def work(nid: str) -> tuple[str, list[IndexDocument] | None]:
            item = source.load(nid)
            if item is None:
                return nid, None
            return nid, self._process_item(item, refresh_nids={nid})

        for nid, docs in parallel_map(nids, work, workers=self._workers):
            if docs is None:  # vanished from disk since the manifest was read
                counts["missing"] += 1
                continue
            if not docs:  # extraction failed after retries; keep the old chunks
                counts["failed"] += 1
                continue
            self._sink.delete_parents({parent_id(source.name, nid)})
            self._sink.upsert(docs)
            counts["reparsed"] += 1
            counts["chunks"] += len(docs)
        log.info("reparsed", source=source.name, **counts)
        return counts


def _query_embedder(config: FundusConfig) -> EmbeddingClient | None:
    """A host-side embedder for queries — only when a model instruct prefix is configured."""
    cfg = config.embedder
    if not cfg.query_prompt:
        return None
    return EmbeddingClient(
        url=cfg.query_url or cfg.url,
        model=cfg.model,
        api_key=cfg.api_key,
        query_prompt=cfg.query_prompt,
    )


def build_sink(config: FundusConfig) -> MeiliSink:
    embedder_cfg = config.embedder
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
        query_embedder=_query_embedder(config),
    )


def resolve_search_key(config: FundusConfig) -> str:
    """The key the MCP server uses to reach Meili: the search-scoped key if set, else the admin key.

    A separate search key only earns its keep once the gateway is split out to a less-trusted
    process; while it's a monolithic host-side server that already holds the admin key, falling back
    to it is fine. Supply a scoped key (search_key / FUNDUS_MEILI_SEARCH_KEY) when you split it out.
    """
    mc = config.meilisearch
    key = mc.search_key or mc.api_key
    if not key:
        raise RuntimeError("no Meili key: set FUNDUS_MEILI_KEY (or a scoped FUNDUS_MEILI_SEARCH_KEY)")
    return key


def build_search_sink(config: FundusConfig) -> MeiliSink:
    """A search-only sink for the MCP server — carries no upsert embedder config; it only queries."""
    return MeiliSink(
        url=config.meilisearch.url,
        index=config.meilisearch.index,
        api_key=resolve_search_key(config),
        embedders=None,
        query_embedder=_query_embedder(config),
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
        embed_cache = SqliteEmbeddingCache(str(config.embed_cache_path()), config.embedder.model)
    lifecycles = build_lifecycles(config.extractor.engines, log_dir=config.logs_dir())
    if lifecycles:
        # A launchd/operator SIGTERM mid-run must still stop the engines this run started.
        install_sigterm_handler()
    return Pipeline(
        config,
        sink or build_sink(config),
        build_extractor(config.extractor.default, config.extractor, lifecycles),
        SqliteExtractionCache(cache_path or str(config.extraction_cache_path())),
        JsonStateStore(state_path or str(config.cursors_path())),
        ocr_languages=list(config.locales),
        workers=config.workers,
        doc_embedder=doc_embedder,
        embed_cache=embed_cache,
        lifecycles=lifecycles,
    )
