# Connecting agents

Fundus exposes its corpus to agents as a **read-only MCP server** with three
tools: `search`, `sources`, `locate`. Any MCP client can use it; OpenClaw and
Claude Code are the worked examples below. Hooking a client up is three steps:

1. run `fundus serve` (persistently),
2. register the endpoint with the client,
3. give the agent the shipped **fundus-search skill** so it knows when and how
   to use the tools.

There is deliberately no installer for step 2 and 3: agent frameworks differ in
where their config lives, which user account they run under, and how they store
secrets. Every step below is a command you run yourself, with
[`fundus connect`](#2-register-the-endpoint) filling in the error-prone values.

## 1. Run the server

The HTTP transports require a bearer token — the credential you share with the
agent. Mint one and put it in your secrets file (the `env_file` your config
points at), or in `[serve].token`:

```bash
openssl rand -base64 32   # → FUNDUS_SERVE_TOKEN=… in your env_file
fundus serve              # streamable-HTTP on 127.0.0.1:8181 by default
```

The server binds only a search-scoped Meilisearch key (never the admin key) and
cannot mutate the index. Host, port, and transport live under `[serve]` in
`fundus.toml`.

To keep it running:

- **macOS** — `fundus service install` includes it as the `<prefix>.serve`
  launchd job (see the README's *Service jobs* section).
- **Linux** — a systemd user unit, e.g. `~/.config/systemd/user/fundus-serve.service`:

  ```ini
  [Unit]
  Description=Fundus read-only MCP server
  After=network.target

  [Service]
  ExecStart=%h/.local/bin/fundus serve
  Restart=on-failure

  [Install]
  WantedBy=default.target
  ```

  then `systemctl --user enable --now fundus-serve`.

## 2. Register the endpoint

`fundus connect` prints ready-to-paste registrations with your real endpoint
and token filled in from config — it changes nothing itself:

```console
$ fundus connect
# OpenClaw
openclaw mcp add fundus --url http://127.0.0.1:8181/mcp --header "Authorization: Bearer …"

# Claude Code
claude mcp add --transport http fundus http://127.0.0.1:8181/mcp --header "Authorization: Bearer …"

# any MCP client (JSON)
{
  "fundus": {
    "type": "http",
    "url": "http://127.0.0.1:8181/mcp",
    "headers": { "Authorization": "Bearer …" }
  }
}
```

Pass a client name for a single, pipeable block: `fundus connect openclaw`.

For OpenClaw, consider also restricting the entry to the three tools in
`openclaw.json` (`mcp.servers.fundus.toolFilter.include = ["search", "sources",
"locate"]`) — the server exposes nothing else, but an explicit allowlist keeps
that true from the client's side even across fundus upgrades.

## 3. Install the skill

[`skills/fundus-search/SKILL.md`](../skills/fundus-search/SKILL.md) tells the
agent what the tools do, how to tune `semantic_ratio` and filters, and how to
open a hit's original. Copy it into your agent's skills directory — for
OpenClaw:

```bash
mkdir -p <workspace>/skills/fundus-search
cp skills/fundus-search/SKILL.md <workspace>/skills/fundus-search/
```

It ships deployment-agnostic. If your agent has per-source skills of its own
(a mail reader, a chat reader), append a short local routing stanza to the
*copy* — not the repo file — mapping your source names to them, e.g.:

```markdown
## Related skills (this deployment)
Route by the hit's `source` field:
- `source = "mail"` → the `mail-search` skill (exact queries, read bodies)
- `source = "chat"` → the `chat-reader` skill (the hit's `ref` is the chat id)
- `source = "documents"` → read the file at `path` directly
```

The copy does not track the repo: after updating fundus, re-copy the skill and
re-apply your stanza.

## 4. Verify

```bash
fundus-client sources            # the thin MCP client: endpoint + token, no Meili keys
fundus-client query "any phrase you know is in the corpus"
openclaw skills check            # fundus-search should be eligible and visible
```

Then ask the agent something only the corpus knows.

## Security posture

- The server binds `127.0.0.1` by default; changing `[serve].host` exposes it
  to the network, gated only by the bearer token — prefer a tunnel or reverse
  proxy with TLS if you must.
- Give the server a search-scoped key (`FUNDUS_MEILI_SEARCH_KEY`) so the
  process holding the port never holds the admin key.
- The tool surface is read-only by construction; the agent can find and read,
  never modify.
- Retrieved corpus content is untrusted data. The shipped skill instructs the
  model to treat it as data, not instructions, but prompt injection from a
  malicious document remains a residual risk of any corpus-reading agent.
