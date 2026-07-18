# Fundus — Decision Record

Short rationale for the major design choices. Each entry is the *why*, not just
the *what*.

## 1. Store: Meilisearch

A single engine that does keyword **and** vector/hybrid search with built-in
embedders, a clean HTTP API, and a first-class read-only key model — a good fit
for a writer/reader split and modest single-host scale. It also avoids a separate
keyword engine plus vector DB.

## 2. One index, with a `source` facet — not per-source, not per-language

A single `corpus` index enables one cross-source query with unified ranking.
Meilisearch's "one index per language" guidance addresses a *partitionable*
catalog and the lexical layer only; a mixed-language corpus queried by an agent
in either language is better served by one index with `localizedAttributes`, with
the vector half covering cross-language and compound-word recall.

## 3. The indexing unit is a chunk

Meilisearch stores one vector per document per embedder, so chunk-level semantic
recall requires each chunk to be its own document, linked to its artifact by
`parent_id` and regrouped at query time.

## 4. Engine-agnostic extraction

Extraction quality is the dominant lever and the best engine is corpus-dependent,
so engines sit behind an `Extractor` interface returning a normalized result.
This makes the engine swappable and A/B-testable; the choice is made empirically
via the bake-off rather than assumed. Transport (REST vs gRPC) is an adapter
detail and intentionally not load-bearing.

## 5. Extraction cache keyed by engine + version + options

Lets multiple engines' outputs for the same document coexist (real A/B), and
makes re-chunking / re-embedding free of re-extraction — important because OCR is
expensive.

## 6. Embeddings: fan-out from the orchestrator, with a vector cache

The default is fan-out indexing: Fundus computes document vectors itself, calling a bare-metal,
OpenAI-compatible endpoint concurrently from the indexing worker pool, then hands Meilisearch the
finished vectors (a `userProvided` embedder). This sidesteps Meilisearch's one-request-at-a-time
REST-embedder path, which is the bottleneck on a heavy local model. A SQLite vector cache (keyed by
model + embed-input text) lets re-indexes reuse embeddings rather than recompute them. The simpler
`rest_embedder` (Meilisearch embeds documents itself) stays available for light models. Either way
the orchestrator only pushes JSON and never imports an ML framework. Queries are always embedded by
Fundus so a model-specific instruct prefix can be applied without polluting the keyword query.

## 7. Docker for services, bare-metal for the model

The search engine and extraction engines containerize cleanly. The embedding
model needs GPU access (unavailable to containers on macOS), so it runs
bare-metal and is reached over HTTP — the one component outside Docker.

## 8. Deterministic ids + upsert; `--full` reconciles against reality

Ids are `hash(source, native_id, chunk_seq)`, so re-indexing is idempotent
(re-reads overwrite, never duplicate). A forward-only cursor can't observe what
happens out of band — deletions, or an in-place edit whose mtime regressed — so a
periodic `--full` pass reconciles. For **append-only stores** (mail, chat) that
means set-difference deletion against the currently-live ids. For **file trees**
it means an rsync-style content reconcile: diff every live file's `(size, mtime)`
fingerprint against the indexed manifest and re-index whatever changed. Size +
mtime (not a content hash) is enough — a real edit moves one or both — and avoids
re-reading every byte of the corpus on each pass. The fingerprint rides as a
stored, non-filterable field, so it needs no index-settings change.

## 9. Per-source cursors in a lock-guarded JSON state file

A simple, proven incremental model (atomic write + file lock, one opaque cursor
per source) avoids a state database and tolerates partial failures by not
advancing the cursor.

## 10. Implementation language: Python

The orchestrator is a thin client to Docker services plus the search index, and
the priority work (extraction bake-off, chunking and index-quality iteration,
LLM-judging) is most ergonomic in Python — which also has the mature client
libraries needed (Meilisearch, MCP) and pushes all heavy ML behind the
service boundary, so the usual "compiled single binary" advantage is moot.

## 11. Chat windowing lives in the chunker

The `ChatChunker` reads a chat's messages from `SourceItem.extra["messages"]`
(the wacli source prepares that per-chat batch); each window's message ids,
participants, and time span ride in `Chunk.meta`. Windowing is a chunking
concern, so chat-shaped sources stay simple emitters.

## 12. Text files bypass the extraction engines

`text/*` files (and Markdown/HTML) are read natively; only binary documents
(PDF/Office) go to docling/tika, and tabular files (`text/csv`, spreadsheets) go
straight to the tabular chunker. No engine round-trip for content that is
already text.

## 13. One `locales` setting

A single top-level config value drives both the index's `localizedAttributes`
and the default OCR languages — the corpus's languages are one fact, declared
once.

## 14. XDG config, one data root

The config file follows `XDG_CONFIG_HOME` (`~/.config/fundus.toml`); all
runtime data is consolidated under one configurable root (`[storage].data_dir`,
default `$XDG_DATA_HOME/fundus`): `meili/`, `cache/`, `state/`. `fundus paths`
reports the resolved locations.
