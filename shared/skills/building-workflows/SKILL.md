---
name: building-workflows
description: Core protocol for building, editing, and debugging nRev workflows with the nrev-workflows MCP tools. Use whenever the user wants to create a workflow, modify an existing one, fix a broken one, or asks what the platform can automate. Load this BEFORE touching any workflow tools; load the domain skills (list-building, qualification-and-disqualification, research, content-generation, gtm-automations, nomination) for the specific objective, and node-settings before configuring any node.
---

# Building nRev workflows

nRev workflows are dataflow graphs: nodes pass tabular data (rows + columns)
along edges. Every workflow run executes nodes in topological order; each node
consumes its parent's output table and emits its own. Credits are charged per
node per row.

## Vocabulary you must get right

- **Start node** (`is_trigger=true`): an entry point of a swimlane. Every
  workflow needs at least one; several are allowed (each starts its own
  swimlane).
- **Listener** (`is_listener=true`): THE automation trigger that makes the
  workflow run on its own (Scheduler cron, "New Gmail message", webhook).
  **At most one per workflow** — the platform enforces it, and edit_workflow
  refuses violations.
- **Handles**: edges carry a source handle and target handle. `_default` for
  straight flow; filter/branch nodes emit on condition handles; the Magic
  Node accepts fan-in on `df1`..`df5`.
- **Single-input rule**: almost every node accepts exactly ONE `_default`
  input. Joining or merging streams requires a **Magic Node** (the AI data
  transform node, 1–5 inputs). edit_workflow enforces this; don't fight it.
- **Test mode** (`is_test_mode`): truncates the node's input during runs so
  iteration is cheap. Keep nodes in test mode while building; flip off only
  for the final full run.

## The protocol

Work in this order. Do not skip steps to "save time" — skipped validation and
testing reliably costs more turns than it saves.

### 1. Understand the objective
Pin down: the entity the workflow operates on (companies? people? content
rows?), the success criteria / qualification rules, the destination of results
(nRev table, Google Sheets, Slack, CRM…), expected volume, and whether it runs
once or on a trigger. If the user's request is ambiguous on any of these,
present your plan in a few bullets and confirm before building. Ask the user
for ICP / persona / competitor / product context when the objective needs it —
there is no tool for tenant context.

### 2. Check for a play first
`search_plays(query)` — a matching template summoned via
`create_workflow(from_play_id=...)` arrives pre-wired; adapting it beats
assembling from scratch. Only build from scratch when no play fits.

### 3. Explore before you design — never fabricate
Your training data does NOT contain this platform's node catalog, settings
field names, or option values. Treat every remembered detail as wrong:

- `search_nodes(query)` to find candidate node types; read their `ai_metadata`.
- `get_node_type(node_definition_id)` for the exact settings schema.
- `get_field_options(...)` for live dropdown values (models, channels,
  spreadsheets, table columns). Use the returned `value`, pass the `label`.
- `list_connections(...)` to confirm an OAuth connection exists for any
  app-backed node BEFORE designing around it; if missing, ask the user to
  connect the app in the platform UI.
- Load the **node-settings** skill before writing any settings dict.

### 4. Design the graph
Decompose into segments: **acquire → normalize → qualify → enrich → act**.
Rules that consistently produce good workflows:

- **Cost-aware ordering**: expensive operations (AI nodes, enrichment,
  paid APIs) run on the smallest possible dataset. Filter and deduplicate
  BEFORE enriching; enrich before AI-scoring; act last.
- Cheap deterministic filters (column operations, dedup) beat AI
  classification when both can express the rule.
- One concern per node; name nodes for what they do ("Filter to ICP titles",
  not "Node 3") — names become `{{column}}` context for humans debugging.
- Branches: qualification nodes split pass/fail on handles — route the fail
  branch somewhere observable (a table) rather than dropping it silently when
  volume matters.
- Transformations between schemas (rename/cast/derive columns, joins,
  group-bys) → **Magic Node**, prompted in plain English. Avoid Custom Code
  unless a Magic Node genuinely cannot express it.

### 5. Build in batches, validate every batch
`edit_workflow(workflow_id, operations=[...])` with `ref` aliases lets you add
a whole segment (nodes + edges) in one call. Then `update_node_settings` for
any node needing configuration beyond what `add_node.settings` covered. After
EVERY batch: read the returned `validation` report (or call
`validate_workflow`) and fix errors immediately — config errors compound and
get harder to attribute.

Use `get_workflow(view="slim")` to re-orient; `view="node"` to read one node's
full settings; never pull `view="full"` unless you truly need every byte.

### 6. Test small, inspect actual data
- Ensure test mode is on (`set_test_mode all=true`).
- For a new/changed node: `run_node(workflow_id, node_id)` — with
  `prior_execution_id` to reuse upstream results instead of re-running them.
- `get_execution(workflow_id, execution_id, wait_seconds=120)` to wait for
  completion in one call.
- **Always inspect output data**, not just statuses:
  `get_node_output(...)` on the changed node. A node can report `completed`
  while every row failed — check the per-row `error` column (Pipedream and
  nrev_tables nodes especially). For large outputs use
  `download_node_output` and analyze the JSONL locally.
- Judge the data against the objective: right columns, right entity, right
  volume, no duplicate rows, qualification splits look sane. Fix → re-run the
  node → re-inspect, until the segment is right. Then move to the next segment.

### 7. Finish
Full test-mode run end to end (`run_workflow`), inspect terminal node outputs,
then flip test mode off and tell the user it's ready. Summarize: what the
workflow does, segment by segment, expected credit consumption drivers, and
what (if anything) the user must do in the UI (connect apps, upload a CSV,
publish/schedule). Don't publish or run full-volume executions without the
user's explicit go-ahead — full runs spend real credits
(`get_auth_status(include_credits=true)` shows the balance).

## Seeding input data

For a small seed list (≲100 rows): `create_table` + `add_table_rows`, then a
`nrev_tables` Query Table node as the workflow's start node. For larger files,
ask the user to upload a CSV via the platform UI. Column names must be
snake_case Python identifiers — they become `{{template}}` references
downstream and break silently otherwise.

## Runtime-configurable values

Values the user should supply per run (a date range, a target persona, a
spreadsheet URL) belong in workflow variables (`manage_variables`), referenced
from node settings — not hardcoded into settings.

## When something fails

1. `get_execution` → which node, what error.
2. `get_node_output` on the failing node AND its parent — most failures are
   schema mismatches: a `{{column}}` reference that doesn't exist upstream,
   a renamed column, a type mismatch (templates resolve to strings; cast
   numerics in an upstream Magic Node).
3. `get_workflow(view="node", node_id=...)` to read the exact settings.
4. Fix with `update_node_settings`, re-run with `run_node` +
   `prior_execution_id`, re-inspect.
