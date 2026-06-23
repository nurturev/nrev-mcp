# Changelog

All notable changes to the `nrev-workflows` plugin. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions are semver and match
the `version` in `plugins/nrev-workflows/.claude-plugin/plugin.json` (the field
Claude Code uses for `/plugin update`).

## [0.7.0]

### Fixed
- **Magic Node could not be configured through the MCP (blocker).** Setting the
  Magic Node's code rejected every payload with "Whoops! Missing a field -
  instructions_and_ref / code_section". Two production-verified shape bugs, both
  now handled for the caller:
  - `code_section` deserializes to a pydantic `CodeSection` model — a bare
    string or empty list is rejected ("Input should be a valid dictionary or
    instance of CodeSection"). It requires the nested
    `[{field_name: …-code, field_value: "<python>"}]` envelope.
  - The Magic Node's input **references must be nested inside the
    `instructions_and_ref` group**, not stored as a top-level setting (3,134 of
    3,136 live Magic Nodes use the nested shape; the top-level form leaves the
    group's required `references` child empty). `edit_workflow` edge-wiring and
    `update_node_settings` now read/write/migrate references in the nested
    location, and `update_node_settings` accepts a `code` (and optional
    `instructions`) shortcut that builds the correct envelopes.
- **`get_field_options` 400 "No valid connection found in settings".** When a
  Pipedream connection was supplied under a mismatched field name (e.g.
  `…-send_message-connection` vs the node's real
  `…-slack_v2_send_message-slack_connection_id`), the dropdown fetch failed.
  `get_field_options` now repairs the connection field name from the node's
  schema (the `app_connection` field) and retries once.

### Added
- **Silent-failure nudges for misconfigured workflows.** A node that runs but
  produces nothing no longer looks "successful":
  - `get_execution` flags `zero_row_nodes` (with a `warnings` note) for nodes
    that completed but emitted 0 rows, starving downstream Slack/Sheets/CRM
    nodes. `run_workflow` guidance now says not to green-light a live run until a
    test run produces rows end to end.
  - `validate_workflow` adds advisory `unconfigured_warnings` for a downstream
    node left with no settings at all (e.g. a Filter with no condition) — the
    platform raises no config error for it, yet it silently drops every row.
    Advisory only: it does not flip `valid`.

## [0.6.0]

### Added
- **Per-node-run execution observability.** `get_execution` now returns the full
  per-block breakdown in `node_runs` (one entry per block execution — a node in
  a loop/fan-out appears once per run, each with its own `node_execution_id`,
  status, duration, `credits_used`, `row_count`, and `error`) plus
  `node_execution_count` (the total blocks executed). It defaults to all runs;
  `only_latest=true` collapses to the latest run per node.
  - **`get_node_output` / `download_node_output` gained `node_execution_id`** — a
    run selector. Pass an id from `node_runs` to read THAT run's rows; `node_id`
    alone still returns the node's latest run. Reaching a non-latest run was
    previously impossible (the by-node endpoint only serves the latest), and
    substituting a foreign execution id 403s — the tool docs now steer to the
    correct flow.
- **`check_node_errors`** — scans a node's output rows for row-level
  `error` / `error_N` values that a node-level `completed` status hides
  (Pipedream-wrapped actions and nrev_tables Add/Update Row nodes report success
  while individual rows failed). Run after writes to catch silent failures.
- **`update_table_rows`** — cell-level PATCH of existing rows by `row_id`
  (`[{row_id, values}]`): only the listed cells change, `null` clears a cell.
  This closes the long-standing gap where the plugin could add rows but not edit
  one (e.g. flip an `is_archived` / "Connection Removed" flag).
- **`delete_table_rows`** — hard-delete up to 1000 rows by `row_id`
  (`confirm=true` gate).
- **Server-side table analytics** — compute over a whole table without pulling
  rows into context:
  - **`aggregate_table`** — count / count_distinct / sum / avg / min / max with
    optional group_by; rewrites response group keys from ids to names.
  - **`get_distinct_values`** — unique values of one column (accepts name or id).
  - **`join_tables`** — inner/left join across tables; rewrites the prefix-keyed
    response (`base.<id>` / `j0.<id>`) to column names, table-prefixed only on
    collision.

### Fixed
- **`get_execution` returned summary-only.** `slim_execution` read the per-node
  list from `node_executions`/`nodeExecutions`, but the platform sends it under
  `blockRuns` — so the entire breakdown was silently dropped and only status /
  credits / timestamps surfaced. Now reads `blockRuns` (with fallbacks).
- **`add_table_rows` keyed values by column NAME**, but the tables service keys
  rows by `column_id` and 400s ("Unknown column") on a name — so name-keyed
  inserts silently failed. Add (and the new update) now resolve names → ids
  against the table schema (id first, then case-insensitive name), matching the
  platform's own Add Row / Update Row nodes.

### Changed
- Tool count 38 → 44.

## [0.5.0]

### Added
- **Tenant awareness + mid-session drift protection.** A user can belong to
  several tenants; the active one is server-side state (resolved per request from
  the session, not encoded in the token), so switching it in the web app makes
  the same session silently start resolving to a different tenant mid-task.
  - **`get_active_tenant`** — reports the tenant work is anchored to plus all the
    tenants the user can switch among; the first call *pins* the active tenant,
    later calls flag `changed_since_pin`. Read-only: this MCP never switches the
    tenant (that stays a web-app action).
  - **`tenant.py`** — in-process pin + TTL-cached active-tenant read + drift check
    over UM's `GET /user/tenants`.
  - **Drift is caught without gating every call.** Mutations on an existing
    resource already fail server-side after a switch (the new tenant can't see
    it), so a 403/404 from the workflow/tables host is diagnosed: if the active
    tenant drifted, the tool raises `TenantChangedError` (stop + inform) instead
    of a confusing access error. Creation tools (`create_workflow`,
    `create_table`) — which spawn a resource with no id for the backend to gate —
    re-verify the tenant *before* creating.

### Changed
- Tool count 37 → 38. Server instructions add a "confirm the tenant" step before
  building, with explicit "halt on tenant change" guidance.

## [0.4.0]

### Added
- **Tenant knowledge base tools.** Read and maintain the company context the
  platform's AI nodes draw on — official website plus ICPs, ideal personas,
  identified competitors, and product offering. Four task-oriented tools (not a
  1:1 mirror of the CRUD routes):
  - **`search_knowledge`** — ranked retrieval (reuses the `find_node` lexical
    ranker) scoped by collection, so you ground a task on only the relevant
    entries instead of dumping the whole KB; empty query lists a collection.
  - **`get_knowledge_base`** — full read annotated with `gaps` (empty slots) and
    `is_usable` (mirrors UM's ≥3/5 filledness rule).
  - **`save_knowledge`** — reconciling merge upsert: matches by id then
    case-insensitive name → update (omitted fields preserved), else add
    (cap-aware; over-cap adds reported, not fatal). Never deletes, so it can't
    silently break a live workflow.
  - **`forget_knowledge`** — guarded delete that surfaces the live-workflow block
    (returns the blocking workflow ids) instead of erroring.
- **`um_api.py`** — first user-management *data* integration beyond auth refresh;
  the tenant is resolved server-side from the session token.

### Changed
- Tool count 33 → 37. Server instructions now point at the knowledge base for
  grounding generated/personalised content.

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
