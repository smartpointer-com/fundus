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

## 8. Deterministic ids + upsert; deletions reconciled separately

Ids are `hash(source, native_id, chunk_seq)`, so re-indexing is idempotent.
Incremental runs cannot reliably observe deletions, so a periodic `--full` pass
reconciles by set-difference against each source's currently-live ids.

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
