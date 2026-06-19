# Fundus — Architecture

Fundus indexes a heterogeneous corpus — email, chat messages, and document files —
into **Meilisearch**, providing full-text and hybrid (keyword + semantic) search
for agents. It is organized around two plugin families — **sources** and
**extraction engines** — both swappable by configuration.

## Design goals

- **Pluggable, configurable sources.** Nothing is hard-coded to a particular
  filesystem layout; all corpus locations come from config.
- **Engine-agnostic extraction.** Swap or A/B-test extraction engines without
  touching the rest of the pipeline.
- **Incremental indexing** with explicit deletion reconciliation.
- **Writer/reader split.** An indexer holds write access; consumers get
  read-only search access.
- **Right component, right place.** The search engine and extraction engines run
  in Docker; the embedding model runs bare-metal and is reached over HTTP.

## Process topology

```
  ┌─────────────────────── Indexer host (writer) ────────────────────────┐
  │  fundus  (Python CLI / orchestrator) — a thin client; holds NO models │
  │     reads the configured source locations                             │
  │        │ HTTP            │ HTTP              │ subprocess / sqlite     │
  │        ▼                 ▼                   ▼                         │
  │  Meilisearch       extraction engine(s)   notmuch CLI / wacli sqlite  │
  │  (Docker)          (Docker: docling/tika)                             │
  │     │ REST embedder call                                              │
  │     ▼                                                                 │
  │  Embedding model server (BARE METAL, OpenAI-compatible /v1/embeddings)│
  │                                                                       │
  │  fundus serve ──▶ MCP server (read-only search key)                   │
  └───────────────────────────────────────────────────────────────────────┘
            ▲ read-only search key (no admin, no write)
            │
     Agent / consumer  (may run as a separate, least-privileged user)
```

## Repository layout

```
src/fundus/
  cli.py            Typer app: init | index | query | serve | sources | bakeoff
  config.py         configuration model + loader (TOML + env)
  models.py         domain models (SourceItem, ExtractionResult/Block, Chunk, IndexDocument)
  log.py            structured logging
  core/             pipeline, state (cursors), ids, reconcile (deletions)
  sources/          base.Source + notmuch / wacli / files connectors + registry
  extract/          base.Extractor + docling / tika / mineru adapters + cache + registry
  chunk/            base.Chunker + text / chat / tabular + dispatch
  index/            base.Sink + meili impl + settings + query
  embed/            builds the Meilisearch REST-embedder config
  serve/            MCP server (read-only search)
  bakeoff/          extraction-engine comparison harness (+ optional LLM judge)
docker/compose.yml  Meilisearch + extraction engines
config/fundus.example.toml
docs/
```

## Core interfaces

| Interface | File | Responsibility |
|---|---|---|
| `Source` | `sources/base.py` | Enumerate a corpus incrementally; list live ids |
| `Extractor` | `extract/base.py` | One adapter per engine; bytes → normalized result |
| `ExtractionCache` | `extract/cache.py` | Content-addressed cache, keyed per engine+version+options |
| `Chunker` | `chunk/base.py` | Normalized result → chunks |
| `Sink` | `index/base.py` | Schema, batched upsert, deletion |
| `StateStore` | `core/state.py` | Per-source cursors (atomic JSON + lock) |

## Domain models

The pipeline passes a small set of typed records (`models.py`):

- **`SourceItem`** — one logical artifact (email, chat window, file). Its
  `payload` is either a `TextPayload` (already textual — bypasses extraction) or
  a `BlobPayload` (binary document — goes through an engine).
- **`ExtractionResult`** — engine-agnostic output: an ordered list of `Block`s
  (heading/paragraph/table/…), a Markdown serialization, and metadata. Rich
  engines populate structure; flat engines degrade to a paragraph stream.
- **`Chunk`** — the indexing unit, linked to its artifact by `parent_id`.
- **`IndexDocument`** — the record stored in the search index.

## Indexing pipeline

`core/pipeline.py` runs, per enabled source:

```
cursor = state.get_cursor(source.name)
for item in source.changed(cursor):
    result = item.payload as result            # text payloads pass through …
             else extract_with_cache(item)     # … blobs go to an engine (cached)
    chunks = dispatch_chunker(item).chunk(result, item)   # text / chat / tabular
    docs   = [to_index_document(item, c) for c in chunks]
    sink.upsert(docs)                          # batched
state.set_cursor(source.name, source.current_cursor())   # atomic
```

A `--full` run additionally reconciles deletions:
`reconcile.delete_missing(source, set(source.live_ids()))`.

Flags: `--only <source>`, `--full`, `--force`, plus an exit-early option for
cheap iteration. A global lock prevents concurrent runs.

## Query & serve

`fundus query` / `fundus serve` build a **hybrid** Meilisearch query
(`semanticRatio` blending keyword + vector) with filters on `source`, `ts`,
`actors`, `tags`, etc., then **group hits by `parent_id`** so the consumer gets
artifacts rather than chunk fragments. `serve` exposes this as an MCP tool using
the read-only search key.

## Search-index design

A **single index** holds all sources, distinguished by a `source` facet:

- **searchable:** `title`, `body` only
- **filterable:** `source, item_kind, ts, actors, tags, path, mime, lang, parent_id`
- **sortable:** `ts`
- **localizedAttributes:** configure the locales present in your corpus (e.g.
  `["eng", "deu"]`).
- **embedders:** one REST embedder pointing at the bare-metal model, with a
  document template over `{{title}} {{body}}`.

Index settings are applied **before the first ingest** (changing them later
triggers a full reindex).

> **One index, not one-index-per-language.** Meilisearch's "one index per
> language" guidance targets a *partitionable* catalog and the lexical layer
> only. A personal/organisational corpus has mixed-language documents and is
> queried by an agent in either language, so a single index with hybrid search
> serves it better; declare locales via `localizedAttributes`, and let the vector
> half carry cross-language and compound-word recall. Enable binary quantization
> if the embedder's dimensionality is large and the index grows past ~1M chunks.

## Extraction: engine-agnostic by design

Document text extraction is the dominant quality lever, and the best engine is
corpus-dependent, so it is deliberately swappable:

- Each engine is one `Extractor` adapter (`docling.py`, `tika.py`, `mineru.py`)
  hiding its transport (REST today; Tika additionally has native gRPC in its 4.x
  line) behind `extract(req) -> ExtractionResult`.
- All adapters return the **same normalized result**, so the chunker is
  engine-agnostic. Structure-rich engines populate `blocks` and tables; flat
  engines degrade gracefully.
- The **cache key includes the engine name + version + options**, so multiple
  engines' outputs for the same file coexist — enabling honest A/B and ensuring
  re-chunking never re-extracts (never re-OCRs).
- `bakeoff/` runs the engines over a representative sample and reports
  (table fidelity, reading order, OCR, speed; optional LLM judge) so the default
  engine is chosen **empirically** on the target corpus.

## Chunking strategy

The indexing unit is a **chunk** (Meilisearch stores one vector per document per
embedder). Granularity differs per item kind, all consuming normalized `blocks`:

- **Documents** — structure-aware chunks on heading/table boundaries with
  overlap, carrying the heading path as context; size-window fallback when no
  structure is present.
- **Chat** — windows of consecutive messages within one conversation, bounded by
  a token budget with overlap; a long silence is a soft cut hint, never a hard
  trigger. Message ids/timestamps are retained so a hit expands back to messages.
- **Spreadsheets / CSV** — per sheet, chunked by row groups with the header row
  repeated in each chunk so it is self-describing.

## Incremental indexing & deletions

Per-source **cursors** (a notmuch lastmod, a max rowid/timestamp, an mtime
window) are persisted in an atomic, lock-guarded JSON state file. Incremental
runs **upsert only**; deletions are reconciled on `--full` by set-difference
against the source's currently-live ids — incremental windows cannot reliably
observe deletes.

## Dependencies

**Runtime:** `typer` (CLI), `pydantic` + `pydantic-settings` (models/config),
`httpx` (+ `tenacity`) for extractor calls, `meilisearch` (official client),
`tokenizers` (chunk budgets), `selectolax` (email HTML→text),
`python-calamine` (spreadsheets), `puremagic` (mime sniffing), `structlog`,
`mcp` (serve). Standard library covers `sqlite3` (chat db, cache), `subprocess`
(notmuch JSON), hashing, and TOML.

Deliberately **absent** from the orchestrator: any ML framework. All heavy
extraction lives behind the Docker/HTTP boundary; all embedding lives in
Meilisearch and the bare-metal model. The Python process stays light.

**Services (Docker):** Meilisearch; one or more extraction engines
(docling-serve, Apache Tika, optionally MinerU). **Bare metal:** the embedding
model on an OpenAI-compatible endpoint.

## Configuration

A single TOML file (`$XDG_CONFIG_HOME/fundus.toml` or `~/.config/fundus.toml`),
env-overridable, with a value-with-env-fallback convention for secrets. See
[`config/fundus.example.toml`](../config/fundus.example.toml). Every source and
path is supplied here — the toolkit ships with no built-in locations.

## Deployment & security

- Run Meilisearch and the extraction engines as containers bound to localhost;
  run the embedding model bare-metal.
- The **indexer uses an admin key**; **consumers use a read-only search key**.
  Consumers can run as a separate, least-privileged OS user with no write path to
  the index and no filesystem access to the corpus.
- When isolating the embedding model from other model servers, you can run it
  under a dedicated account while **sharing the model weights on disk** to avoid
  duplicating them across installations.
