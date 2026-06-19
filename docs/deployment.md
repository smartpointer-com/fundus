# Deployment & machine-specific notes

Fundus is environment-agnostic: this repository contains no paths, hostnames, or
data specific to any one machine. Your deployment specifics — where your corpus
lives, which embedding endpoint you run, which hosts your services bind to —
belong **outside** version control.

## Where to keep them

Put your machine-specific deployment notes in:

```
~/.config/fundus/DEPLOYMENT.local.md
```

This path is intentionally outside the repository, and `*.local.md` is
git-ignored so the file stays out even if you keep it inside a checkout. Keep your
runtime configuration next to it (default `~/.config/fundus.toml`; see
[`config/fundus.example.toml`](../config/fundus.example.toml)).

## Why — and the code-assistant angle

If you use an AI coding assistant or agent, **point its context at
`~/.config/fundus/DEPLOYMENT.local.md`** (for example, reference that path from
your `CLAUDE.md`, `AGENTS.md`, or equivalent). The assistant then knows your real
paths, ports, and hosts without any of them leaking into the shared repository —
keeping the project portable for everyone else.

## Template

Copy this into `~/.config/fundus/DEPLOYMENT.local.md` and fill in your specifics:

```markdown
# Fundus — local deployment

## Sources
- mail (notmuch):    db    = /path/to/maildir
- chat (wacli):      db    = /path/to/whatsapp.db
- documents (files): roots = [/path/to/docs, ...]

## Services
- Meilisearch:     http://127.0.0.1:7700   (admin key via FUNDUS_MEILI_KEY)
- docling-serve:   http://127.0.0.1:5001
- tika:            http://127.0.0.1:9998
- Embedding model: http://127.0.0.1:8081/v1/embeddings  (bare-metal; model = ...)

## Consumers
- Read-only search key for: <who/what queries the index>

## Operations
- Schedule: <how/when incremental + full reconcile run>
- Notes:    <container runtime, mounts, GPU, scheduling>
```
