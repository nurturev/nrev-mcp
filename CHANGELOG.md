# Changelog

All notable changes to the `nrev-workflows` plugin. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are semver and match
the `version` in `plugins/nrev-workflows/.claude-plugin/plugin.json` (the field
Claude Code uses for `/plugin update`).

## [0.3.0]

### Added
- **Persistent, auto-refreshing auth — no more pasting a JWT.** New **`auth_login`**
  tool and **`nrev-workflows auth login|logout|status`** CLI (wrapped by
  `plugins/nrev-workflows/bin/login.sh`). Sign in once via the browser through the
  platform web app; the resulting Supabase session is relayed to the CLI and stored
  at `~/.nrev-workflows/credentials` (chmod 600), then refreshed automatically via
  user-management. The session is a genuine Supabase token, so the workflow API and
  tables service accept it directly.
- **Environment selection** — `NREV_ENV=prod|staging` (default `prod`); the server
  otherwise follows the logged-in session's environment, so `auth login --staging` is
  the only place you choose it. Hosts overridable individually (`NREV_WEBAPP_URL`,
  `NREV_UM_URL`, `NREV_WF_HOST`, `NREV_TABLES_HOST`).

### Changed
- `auth.py` is now a persistent, relay-based session store with a manual override.
  `set_jwt` / `NREV_JWT` remain as opaque, non-refreshed overrides and take precedence
  when set. `get_auth_status` reports the auth source (`session`/`manual`), identity,
  environment, and a session/environment mismatch warning; `transport` refreshes and
  retries once on a 401. Tool count 32 → 33 (added `auth_login`).

### Requires
- The companion platform endpoints: user-management `POST /auth/cli/refresh` and the
  `target=workflow` branch of `POST /auth/cli/complete`, plus the web app
  `/cli/auth/done` change that forwards the Supabase session to the CLI.

## [0.2.0]

### Added
- **`find_node(intent)`** — intent-ranked catalog search (synonym-aware lexical
  scoring in `ranking.py`); surfaces the right node without guessing its name.
- **`describe_node(...)`** — returns a node's settings schema, AI metadata,
  materialized fields, AND pre-fetched live dropdown options in one round trip
  (collapses `get_node_type` + N×`get_field_options`).
- **`estimate_run_cost(workflow_id, rows)`** — upper-bound credit estimate with
  per-node breakdown and top cost drivers.
- **`workflow-examples` skill** — complete, correctly-shaped reference builds
  (linear pipeline, Magic Node fan-in, listener→research→notify).
- **`troubleshooting` skill** — symptom→cause→fix catalog incl. silent-failure
  modes, validation/OperationError refusals, and the full APIError status table.
- **`scripts/bump-version.sh`** — sets the version in lockstep across
  plugin.json, marketplace.json, and pyproject (then re-syncs the bundle).

### Fixed
- Corrected the marketplace-install command and plugin `homepage` to the real
  repo owner `nurturev/nrev-mcp` (were pointing at the non-existent
  `nurturev-dev/nrev-mcp`). The predecessor link stays at
  `nurturev-dev/nrev-workflow-mcp`, which is where that repo actually lives.

### Changed
- **`run_workflow` spend gate (behavior change):** a run with any live
  (non-test-mode) node is now refused unless `confirm=true`, returning the cost
  estimate and the list of live nodes instead. Fully test-mode workflows still
  run without confirmation.
- Server instructions route discovery through `find_node`→`describe_node` and
  point at the two new skills.
- Tool count 29 → 32; skill count 8 → 10. README and manifest descriptions updated.

## [0.1.0]
- Initial POC: 27→29 task-oriented MCP tools (workflow platform + tables
  service), batched `edit_workflow` mutation engine with invariant enforcement,
  compact projections, and 8 domain skills. stdio transport, per-user JWT.
