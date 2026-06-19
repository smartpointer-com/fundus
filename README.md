# Fundus

Fundus is a self-hostable toolkit that indexes a heterogeneous corpus — **email,
chat messages, and document files** — into [Meilisearch](https://www.meilisearch.com/),
giving you full-text **and** hybrid (keyword + semantic) search designed for
consumption by agents.

It is built around two plugin families, both swappable by configuration:

- **Sources** — where data comes from (a notmuch mail store, a chat database, a
  file tree). Nothing is hard-coded to a particular filesystem layout.
- **Extraction engines** — how document bytes become text. An engine-agnostic
  interface lets you swap or A/B-test engines (docling-serve, Apache Tika, MinerU)
  without touching the rest of the pipeline.

## How it fits together

```
sources ──▶ extraction (engine-agnostic) ──▶ chunking ──▶ Meilisearch ──▶ agents
 mail        docling-serve / tika / ...        text /                       (read-only
 chat         (Docker, swappable)              chat /        ▲               search)
 files                                         tabular       │
                                                     embeddings via a bare-metal,
                                                     OpenAI-compatible model endpoint
```

- The **search engine and extraction engines run in Docker**; the **embedding
  model runs bare-metal** and is reached over HTTP.
- The indexer writes with an admin key; **consumers search with a read-only key**.

See [docs/architecture.md](docs/architecture.md) for the full design,
[docs/implementation-plan.md](docs/implementation-plan.md) for the build phases,
[docs/decisions.md](docs/decisions.md) for the rationale behind the major
choices, and [docs/deployment.md](docs/deployment.md) for where to keep
machine-specific setup notes.

## Quick start (once implemented)

```bash
make install                       # set up the Python environment
cp config/fundus.example.toml ~/.config/fundus.toml   # then edit paths
make up                            # start Meilisearch + extraction engines
fundus init                        # apply index settings
fundus index                       # ingest (incremental)
fundus serve                       # expose read-only search over MCP
```

## Local deployment notes

Keep your machine-specific wiring (corpus paths, service hosts/ports, the
embedding endpoint) in `~/.config/fundus/DEPLOYMENT.local.md` — outside this repo,
which stays generic. If you use an AI coding assistant, point its context at that
file so it knows your environment without leaking it into the repository. See
[docs/deployment.md](docs/deployment.md).

## Status

Early scaffold: interfaces, configuration, and documentation are in place; the
connectors, extraction adapters, chunkers, and sink are being implemented per the
[implementation plan](docs/implementation-plan.md).

## License

To be determined.
