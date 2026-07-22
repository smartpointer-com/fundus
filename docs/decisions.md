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
expensive. The version in the key is a config pin, not the live engine version —
see decision 16.

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

## 15. OCR engine is config, not platform code; Apple Vision runs bare-metal

The extraction engines run in Docker, but a Linux container can reach neither a
macOS GPU nor Apple's Vision framework — the same wall that keeps the embedding
model bare-metal. So the container's OCR falls back to a CPU engine (EasyOCR),
which on a mixed German/English scanned corpus is both slower and weaker
(strips umlauts, misreads figures) than Apple Vision.

Rather than branch on platform in code, the OCR engine is a single config field,
`[extractor.engines.docling-serve].ocr_engine`, sent per request (default: unset,
so docling-serve picks its own — the portable behavior). To use Apple Vision, run
docling-serve **bare-metal** on the host (like the embedder) and set
`ocr_engine = "ocrmac"`; the adapter is otherwise unchanged. The rasterization
resolution that most affects OCR fidelity is not exposed over docling-serve's HTTP
API, so fundus uses docling's default (216 DPI) — measured as the best single
value across document types (higher DPI helps dense scans but regresses others,
non-monotonically), which is the right call for an engine serving a heterogeneous
corpus.

The bare-metal server's lifecycle is managed as an opt-in `<prefix>.docling`
launchd job (`[service.docling]`) so one `fundus service install` covers it
alongside indexing and the MCP server. The launch command and its environment
stay in the user's config, not the repo — this stays a generic toolkit.

## 16. Extraction provenance in the index; the cache never auto-invalidates

Two levers exist for "the indexed text is stale even though the file didn't
change", and they are deliberately different mechanisms:

**Deliberate config changes converge automatically.** Every document carries an
`extract_sig` — the engine that produced its text plus that engine's
output-affecting settings (`docling-serve:ocrmac`), stamped at extraction time
and read back as part of the `--full` manifest. When configuration changes (a
new `ocr_engine`), the nightly full reconcile finds the affected documents by
sig drift and re-parses exactly those, bypassing the extraction-cache *read*
(the cached row is the stale artifact; the fresh result overwrites it in
place). Only documents produced by the changed engine re-parse — the escalating
router's tika-served majority is untouched by a docling re-tune.

**Engine upgrades do not auto-invalidate.** Engines update frequently and
mostly without output-visible effect, so neither the cache key nor the sig
tracks live engine versions — an upgrade alone re-OCRs nothing (auto-invalidating
on version would be overly conservative and turn routine upgrades into
surprise corpus-wide re-OCR nights). When an upgrade IS judged worth it, that's
a human call, made explicit: `fundus reparse --ocr-only` (or `--path-prefix`,
`--source`) re-extracts the selection through the same cache-bypassing refresh.
`reparse` deletes a document's old chunks only after its fresh extraction
succeeded, so an engine outage mid-run degrades to "nothing happened", never to
lost documents. (Manual pinning via the engine `version` config field remains
as a blunt fallback lever.)
