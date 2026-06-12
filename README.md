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
│       │   ├── server.py             # entrypoint (stdio)
│       │   ├── app.py                # FastMCP instance + server instructions
│       │   ├── auth.py               # in-memory JWT store (never persisted)
│       │   ├── transport.py          # shared HTTP core
│       │   ├── api.py                # workflow platform REST wrappers
│       │   ├── tables_api.py         # tables service REST wrappers
│       │   ├── shapes.py             # envelope construction + edit_workflow op engine
│       │   ├── projections.py        # compact views of large API payloads
│       │   └── tools_*.py            # 32 MCP tools in 5 modules
│       └── tests/                    # pure-logic unit tests (no network)
├── plugins/
│   └── nrev-workflows/               # Claude plugin: MCP config + 10 skills
│       ├── .mcp.json                 # launches bin/run-mcp.sh (stdio via uv)
│       └── skills/                   # building-workflows, node-settings,
│                                     # workflow-examples, troubleshooting,
│                                     # list-building, qualification…,
│                                     # research, content-generation,
│                                     # gtm-automations, nomination
└── scripts/sync-plugin.sh            # bundle server into plugin for releases
```

## Install (Claude Code)

```
/plugin marketplace add nurturev/nrev-mcp
/plugin install nrev-workflows@nrev
```

Prereqs: Python 3.10+ and [uv](https://docs.astral.sh/uv/). Restart Claude
Code after install; `/mcp` should show `nrev-workflows` with 32 tools.

First use, once per session: grab a JWT from the platform web app (DevTools →
Network → `Authorization` header) and tell Claude *"set my nrev JWT to
eyJ..."* — or export `NREV_JWT` before launching. Tokens last ~12 h and live
in process memory only.

### Dev install (this repo cloned locally)

```
claude mcp add nrev-workflows --scope user -- /path/to/nrev-mcp/plugins/nrev-workflows/bin/run-mcp.sh
```

The launcher prefers `servers/workflows` from the repo checkout, falling back
to the bundled copy under `plugins/nrev-workflows/mcp/` (created by
`scripts/sync-plugin.sh` — run it before tagging a release).

## Versioning & releases

The authoritative version for **Claude Code** update detection is the `version`
in `plugins/nrev-workflows/.claude-plugin/plugin.json` — it wins over the
marketplace entry, and `/plugin update` does a semver comparison against it
(strict `MAJOR.MINOR.PATCH`, no leading `v`). **Claude Cowork** instead tracks
the git commit of the synced repo, so a fresh tagged commit is what it picks up;
the version field is informational there. The bundled MCP server's
`pyproject.toml` version is invisible to Claude Code — kept aligned only for
tidiness / eventual PyPI publish.

To cut a release, bump all four version fields in lockstep and tag:

```
scripts/bump-version.sh 0.3.0     # plugin.json + marketplace.json + pyproject, then re-syncs bundle
# update CHANGELOG.md
git commit -am "Release 0.3.0" && git tag v0.3.0 && git push --follow-tags
```

Never hand-edit one version field alone — divergence between plugin.json and
the marketplace entry is silent (plugin.json wins). The script is the single
entry point.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `NREV_JWT` | – | Seed the JWT at startup (else use the `set_jwt` tool) |
| `NREV_WF_HOST` | `https://workflow.public.prod.nurturev.com` | Workflow platform API |
| `NREV_TABLES_HOST` | `https://nrev-tables-service.public.prod.nurturev.com` | Tables service |
| `NREV_TIMEOUT` | `60` | HTTP timeout (seconds) |
| `NREV_DOWNLOAD_DIR` | `~/.nrev-mcp/downloads` | download_node_output target |

## Tool surface (32)

| Group | Tools |
|---|---|
| Auth | `set_jwt`, `get_auth_status` |
| Discovery | `search_nodes`, `find_node` (intent-ranked search), `get_node_type`, `describe_node` (schema + live options in one call), `get_field_options`, `list_connections`, `search_plays` |
| Workflows | `list_workflows`, `get_workflow`, `create_workflow`, `edit_workflow` (batched graph ops), `update_node_settings`, `manage_variables`, `set_workflow_live`, `get_workflow_live_status` |
| Execution | `validate_workflow`, `estimate_run_cost`, `run_workflow` (spend-gated), `run_node`, `get_execution` (with wait), `stop_execution`, `get_node_output`, `download_node_output` |
| Tables | `list_tables`, `get_table`, `create_table`, `update_table`, `delete_table`, `get_table_rows`, `add_table_rows` |

Design notes:
- `find_node` ranks the whole catalog against a natural-language intent
  (synonym-aware lexical scoring in `ranking.py`) so node discovery doesn't
  depend on guessing the exact name; `describe_node` returns a node's settings
  schema **and** pre-fetches every dropdown's live options in one round trip.
- `run_workflow` refuses a real-credit run (any node not in test mode) without
  `confirm=true`, returning an `estimate_run_cost` breakdown so spend is
  surfaced before it happens.
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
- `POST .../abort` (predecessor flagged it may 404)
- Orphan-target `inputs` skeleton on edge wiring (shapes.py `_op_add_edge`)
- `DELETE /tables/{id}` (was 405 "not yet live" at predecessor's last test)

## Testing

```
cd servers/workflows && uv run pytest
```

24 unit tests cover the mutation engine, projections, and auth — pure
functions, no network. Live-API smoke testing is manual for now (POC).

## Roadmap

- Remote Streamable HTTP transport + OAuth (Cowork connectors directory,
  customer distribution) — the transport is isolated in `transport.py`/`app.py`
  so this is additive.
- Second server: full tables/dashboards surface as its own plugin.
- Eval harness: drive the workflow_studio WBA evaluation datasets through
  Claude Code + this plugin and score with the same judge/rubric.
- Per-user OAuth replacing JWT-paste; seed-CSV upload once the platform
  exposes a presigned-URL endpoint.
