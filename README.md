# nrev-mcp

NurtureV's MCP monorepo: one repository for the MCP servers and Claude plugins
that expose our backends to AI agents (Claude Code, Claude Cowork, Claude.ai).

**Status: POC.** Successor to [nrev-workflow-mcp](https://github.com/nurturev-dev/nrev-workflow-mcp)
(78 flat tools, knowledge in unreachable docs) — rebuilt with a consolidated
task-oriented tool surface and the domain knowledge of the internal
workflow-builder agent shipped as plugin **skills**.

```
nrev-mcp/
├── .claude-plugin/marketplace.json   # plugin marketplace catalog
├── servers/
│   └── workflows/                    # MCP server: workflow platform + tables service
│       ├── src/nrev_workflows_mcp/
│       │   ├── server.py             # stdio entrypoint (local / Claude Code CLI)
│       │   ├── server_http.py        # hosted entrypoint (streamable-http + OAuth, e.g. Cowork)
│       │   ├── app.py                # FastMCP instance + server instructions
│       │   ├── auth.py               # persistent auto-refreshing session (local file or hosted session store)
│       │   ├── oauth.py              # OAuth 2.1 authorization server for the hosted transport
│       │   ├── session_store.py      # Redis-backed session/token store (hosted transport only)
│       │   ├── request_state.py      # per-request identity seam (hosted transport only)
│       │   ├── config.py             # env (prod/staging) + host + credential-path resolution
│       │   ├── login.py              # browser sign-in relayed via the web app (stdio transport)
│       │   ├── cli.py                # `nrev-workflows auth login|logout|status`
│       │   ├── transport.py          # shared HTTP core (refresh + retry on 401)
│       │   ├── api.py                # workflow platform REST wrappers
│       │   ├── tables_api.py         # tables service REST wrappers
│       │   ├── shapes.py             # envelope construction + edit_workflow op engine
│       │   ├── projections.py        # compact views of large API payloads
│       │   ├── um_api.py             # user-management REST wrappers (tenancy + knowledge base)
│       │   ├── tenant.py             # active-tenant pin + mid-session drift detection
│       │   └── tools_*.py            # 69 MCP tools in 10 modules
│       ├── Dockerfile                # image for the hosted (streamable-http) transport
│       └── tests/                    # pure-logic unit tests (no network)
├── shared/                           # ⭐ single source of truth (edit here)
│   ├── skills/                       # 15 SKILL.md domain skills (open format)
│   └── AGENTS.md                     # always-on agent context (thin)
├── packages/                         # one uniform package per agent (committed)
│   ├── claude/                       # Claude: .claude-plugin/ + .mcp.json + bin/ + skills/ + mcp/
│   ├── codex/                        # Codex:  .codex-plugin/ + .mcp.json + bin/ + skills/ + mcp/
│   └── gemini/                       # Gemini: gemini-extension.json + skills/ + mcp/
└── scripts/
    ├── sync-agents.sh                # ⭐ fan shared/ → every agent package (skills/ + mcp/)
    └── bump-version.sh               # stamp versions in lockstep
```

## Install (Claude Code)

> **New here?** [`docs/quickstart.md`](docs/quickstart.md) walks the whole
> path — install, sign in, first data pull — in about five minutes.

```
/plugin marketplace add nurturev/nrev-mcp
/plugin install nrev-workflows@nrev
```

**No local runtime required.** Since v1.0.0 all three agent manifests point at
the hosted prod connector (`.mcp.json` → `type: "http"`, `oauth: true`,
`https://nrev-workflows-mcp.public.prod.nurturev.com/mcp`), so the installed
plugin spawns no process — nothing to install, no Python, no uv. Restart the
client after install; `/mcp` should show `nrev-workflows` (69 tools).

Sign-in is the client's normal OAuth "Connect" prompt (`/mcp` → connect
`nrev-workflows` if it doesn't appear on its own), relayed through the platform
web app and refreshed automatically. The hosted entrypoint is
`nrev-workflows-mcp-http` (`servers/workflows/server_http.py`); per-user
sessions live in the Redis-backed store, so no credentials touch the client.

Python 3.10+ and [uv](https://docs.astral.sh/uv/) are needed only for the **dev
install** below, which runs the server locally from source. That path uses the
local stdio transport and its own file-backed session
(`~/.nrev-workflows/credentials`, chmod 600) via `auth_login` or
`scripts/login.sh`; production by default, `NREV_ENV=staging` to switch.

```
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows (PowerShell) — or: winget install --id=astral-sh.uv
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### Dev install (this repo cloned locally)

```
# macOS / Linux — run-mcp.sh prefers the live servers/workflows checkout
claude mcp add nrev-workflows --scope user -- /path/to/nrev-mcp/scripts/run-mcp.sh

# Windows — run-mcp.sh is a bash script and can't be spawned; point uv at the source directly
claude mcp add nrev-workflows --scope user -- uv run --project C:\path\to\nrev-mcp\servers\workflows nrev-workflows-mcp
```

On macOS/Linux the `run-mcp.sh` launcher prefers `servers/workflows` from the
repo checkout (live source), falling back to the bundled copy under
`packages/claude/mcp/`. On Windows, point `uv --project` at
`servers\workflows` directly for the same live-source dev loop. The bundled
copy is created by `scripts/sync-agents.sh` — run it before tagging a release.
(Installed plugins always run the bundled copy anyway: the extracted plugin has
no `servers/` sibling, so the marketplace-install path is unaffected by the OS.)

## Multi-agent support (Codex CLI, Gemini CLI)

> **Full architecture & maintenance guide:** [`docs/multi-agent-packaging.md`](docs/multi-agent-packaging.md).

The MCP server is a standard **stdio** server, so it is client-agnostic — Codex
and Gemini speak the same protocol as Claude Code. The 15 domain skills are in
the open **Agent Skills** (`SKILL.md`) format that Claude Code, Codex, and
Gemini all read (same `name`/`description` frontmatter, same progressive
disclosure). So skills and docs are authored **once** in `shared/` and fanned
out to one uniform package per agent under `packages/`:

```
scripts/sync-agents.sh            # --build (default): regenerate all packages
scripts/sync-agents.sh --link-dev # symlink shared/skills into each agent's live
                                   # skills dir (~/.claude, ~/.agents, ~/.gemini)
```

- **Claude Code** — `packages/claude/`. Installed via the plugin marketplace
  (`nrev-workflows@nrev`); the marketplace `source` points here.
- **Codex CLI / app** — `packages/codex/`, a native Codex plugin:
  `codex plugin marketplace add https://github.com/nurturev/nrev-mcp.git`, then
  `codex plugin add nrev-workflows@nrev` — see
  [`packages/codex/README.md`](packages/codex/README.md).
- **Gemini CLI** — `packages/gemini/`. `gemini extensions install
  ./packages/gemini` — see [`packages/gemini/README.md`](packages/gemini/README.md).

**Editing rule:** only ever edit `shared/skills/` and `shared/AGENTS.md`, then
run `sync-agents.sh`. Within each `packages/<agent>/`, `skills/` and `mcp/` are
generated; the manifest (`.claude-plugin/` / `.codex-plugin/` + `.mcp.json` /
`gemini-extension.json`) is hand-authored. All of it is committed (installers
fetch from the repo) — but never hand-edit `skills/` or `mcp/`. The Codex
plugin path is smoke-tested against a live install; one packaging detail still
needs a live smoke test before external distribution: Gemini's subdir install +
`${extensionPath}` variable (the package README flags it).

## Versioning & releases

The authoritative version for **Claude Code** update detection is the `version`
in `packages/claude/.claude-plugin/plugin.json` — it wins over the
marketplace entry, and `/plugin update` does a semver comparison against it
(strict `MAJOR.MINOR.PATCH`, no leading `v`). **Claude Cowork** instead tracks
the git commit of the synced repo, so a fresh tagged commit is what it picks up;
the version field is informational there. The bundled MCP server's
`pyproject.toml` version is invisible to Claude Code — kept aligned only for
tidiness / eventual PyPI publish.

To cut a release, bump every version field in lockstep and tag:

```
scripts/bump-version.sh 0.3.0     # plugin.json + marketplace.json + pyproject +
                                  # codex/gemini manifests, then re-syncs all packages
# update CHANGELOG.md
git commit -am "Release 0.3.0" && git tag v0.3.0 && git push --follow-tags
```

Never hand-edit one version field alone — divergence between plugin.json and
the marketplace entry is silent (plugin.json wins). The script is the single
entry point.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `NREV_ENV` | `prod` | Environment (`prod`/`staging`); derives the web-app, UM, workflow & tables hosts. The server otherwise follows the logged-in session's env. |
| `NREV_WEBAPP_URL` | per `NREV_ENV` | Web app base — where sign-in is relayed from (overrides `NREV_ENV`) |
| `NREV_UM_URL` | per `NREV_ENV` | user-management base — session refresh (overrides `NREV_ENV`) |
| `NREV_WF_HOST` | per `NREV_ENV` | Workflow platform API (overrides `NREV_ENV`) |
| `NREV_WF_MCP_URL` | `<workflow host>/mcp` | workflow_studio's data MCP server (one-off data tools federation) |
| `NREV_TABLES_HOST` | per `NREV_ENV` | Tables service (overrides `NREV_ENV`) |
| `NREV_WORKFLOWS_DIR` | `~/.nrev-workflows` | Where the session credentials are stored |
| `NREV_TIMEOUT` | `60` | HTTP timeout (seconds) |
| `NREV_DOWNLOAD_DIR` | `~/.nrev-mcp/downloads` | download_node_output target |

## Tool surface (69)

| Group | Tools |
|---|---|
| Auth | `auth_login` (browser sign-in, auto-refresh), `get_auth_status` |
| Tenant | `get_active_tenant` (which tenant work is anchored to + the ones the user can switch among; read-only — never switches) |
| Discovery | `search_nodes`, `find_node` (intent-ranked search), `get_node_type`, `describe_node` (schema + live options in one call), `get_field_options`, `list_connections`, `connect_app` (mint the hosted OAuth URL for a new app account), `search_plays` |
| Workflows | `list_workflows`, `get_workflow`, `create_workflow`, `duplicate_workflow`, `edit_workflow` (batched graph ops), `update_node_settings`, `manage_variables`, `set_workflow_live`, `get_workflow_live_status`, `export_workflow` (full JSON to a local file), `import_workflow` (from an export file), `set_workflow_tags`, `add_workflow_tag`, `remove_workflow_tag` |
| Execution | `validate_workflow`, `estimate_run_cost`, `run_workflow` (spend-gated), `run_node`, `get_execution` (with wait), `stop_execution`, `stop_node_execution`, `resume_execution`, `list_recent_executions` (global run history), `get_execution_stats`, `get_node_output`, `download_node_output`, `check_node_errors` |
| Listeners | `activate_listener_test` (arm a webhook/trigger node), `get_listener_event` (poll for the captured payload), `deactivate_listener` |
| One-off data | `list_data_tools` (federated live from the workflow_studio MCP server), `run_data_tool` (server-enforced spend gate: confirm=false returns a credit estimate), `save_to_table` (land results in an nRev Table, creating it if needed) |
| Tables | `list_tables`, `get_table`, `create_table`, `update_table`, `delete_table`, `duplicate_table`, `get_table_rows`, `add_table_rows`, `update_table_rows`, `delete_table_rows`, `clear_table_rows`, `aggregate_table`, `get_distinct_values`, `join_tables`, `set_table_tags`, `add_table_tag`, `remove_table_tag` |
| Tags | `list_tags`, `create_tag`, `update_tag`, `find_or_create_tag` (address a tag by name instead of raw UUID) |
| Knowledge base | `search_knowledge` (ranked retrieval), `get_knowledge_base` (full read + gaps), `save_knowledge` (reconciling merge upsert), `forget_knowledge` (guarded delete) |

Design notes:
- `find_node` ranks the whole catalog against a natural-language intent
  (synonym-aware lexical scoring in `ranking.py`) so node discovery doesn't
  depend on guessing the exact name; `describe_node` returns a node's settings
  schema **and** pre-fetches every dropdown's live options in one round trip.
- `run_workflow` refuses a real-credit run (any node not in test mode) without
  `confirm=true`, returning an `estimate_run_cost` breakdown so spend is
  surfaced before it happens.
- **One-off data is federated, not hand-written.** The workflow_studio MCP
  server exposes each tool-eligible node as a tool with a server-enforced
  spend gate; nrev-mcp connects to it per call (Streamable HTTP, same platform
  JWT) and re-exposes the set — so new data tools appear without an nrev-mcp
  release, and the confirm/estimate round-trip is enforced in one place,
  server-side.
- **Tenant safety.** A user can belong to several tenants; the active one is
  server-side state (not in the token), so switching it in the web app makes the
  same session resolve to a different tenant mid-task. `get_active_tenant` pins
  the tenant work is anchored to; the MCP never switches it. Drift is then caught
  two ways without gating every call: creation tools (`create_workflow`,
  `create_table`) re-verify before spawning a resource the backend can't yet
  access-gate, and a 403/404 from the workflow/tables host is diagnosed — if the
  active tenant has drifted, the tool raises a clear `TenantChangedError` (stop
  and inform the user) instead of a confusing access error.
- `edit_workflow` replaces the predecessor's 8 mutation micro-tools with one
  batched operation engine (`servers/workflows/src/nrev_workflows_mcp/shapes.py`)
  that enforces the platform invariants: single-input rule, Magic Node df1–df5
  fan-in with auto-maintained references, one listener per workflow,
  action-types-can't-be-roots, trigger flips when wiring into a start node.
- Reads are projected to compact views (`projections.py`); full payloads only
  on explicit `view="full"`.
- Domain knowledge ships as plugin **skills** (progressive disclosure), not
  tool-docstring walls or repo docs the installed agent can't read.

## Endpoints to verify on first live run

Most endpoint paths/shapes were inherited from the production-verified
predecessor. The following are new here and were taken from the backend route
definitions but not yet exercised — if one fails, capture the request from
the platform web app's network tab and fix the wrapper in `api.py`:

- `POST /executions/workflow/{id}/execute` body key for manual input data
- `GET /plays/multi` query params; `POST /plays/{id}/summon` body
- `/workflow/{id}/variables` create/update body shape
- Orphan-target `inputs` skeleton on edge wiring (shapes.py `_op_add_edge`)
- `DELETE /tables/{id}` (was 405 "not yet live" at predecessor's last test)
- The workflow_studio data MCP server itself (`<workflow host>/mcp`) — the
  federation client (`list_data_tools` / `run_data_tool`) is built to the
  agreed contract but the upstream server may not be deployed in every
  environment yet; the tools report this actionably when unreachable

(1.0.0 cleared the former `POST .../abort` entry: the platform's stop path is
`POST .../workflow-execution/{id}/stop`, verified against the web app.)

## Testing

```
cd servers/workflows && uv run pytest
```

127 unit tests cover the mutation engine, projections, auth (session
persistence + refresh, network mocked), the workflow-builder-parity client
methods, and the one-off data tools (upstream MCP session faked) — no live
calls. Live-API smoke testing is manual for now (POC).

## Roadmap

- Remote Streamable HTTP transport + OAuth (Cowork connectors directory,
  customer distribution) — the transport is isolated in `transport.py`/`app.py`
  so this is additive.
- Second server: full tables/dashboards surface as its own plugin.
- Eval harness: drive the workflow_studio WBA evaluation datasets through
  Claude Code + this plugin and score with the same judge/rubric.
- ~~Per-user OAuth replacing JWT-paste~~ — done (v0.3.0): browser sign-in relayed
  through the platform web app + user-management, with automatic refresh.
  Seed-CSV upload once the platform exposes a presigned-URL endpoint.
