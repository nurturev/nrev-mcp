---
name: workflow-examples
description: Complete, correctly-shaped reference builds for nRev workflows — the exact edit_workflow operations[] arrays and node settings for canonical patterns (linear enrich→qualify→write pipeline, Magic Node fan-in/join, listener-triggered research→notify). Load alongside building-workflows when assembling a workflow from scratch, to copy a known-good structure instead of inferring one. The type_ids shown are PLACEHOLDERS — always resolve real node_definition_ids via find_node/describe_node first.
---

# Workflow Examples

Copy these structures; do not copy the `type_id` values. Every `type_id` below
is a placeholder of the form `<find_node("…")>` — resolve the real
`node_definition_id` with `find_node` / `describe_node` against the live catalog
before building. Settings field names are illustrative too: confirm each with
`describe_node` (it returns the schema AND the live dropdown options).

Conventions that hold across all examples:
- `ref` aliases let one `edit_workflow` call wire a whole segment; later ops
  reference an earlier node by its ref before its real UUID exists.
- Column names are **snake_case** identifiers — they become `{{template}}`
  references downstream and break silently otherwise.
- Templates resolve to **strings**; cast numerics in an upstream Magic Node.
- Build with everything in test mode, then flip off for the final run.

---

## 1. Linear pipeline — seed → enrich → qualify → write

The canonical shape: a start node emits rows, each downstream node consumes its
parent's output. Cost-aware ordering — cheap filter before paid enrichment,
enrichment before AI scoring, write last.

```json
[
  {"op": "add_node", "ref": "src", "type_id": "<find_node('query nrev table')>",
   "name": "Load seed companies",
   "settings": {"<table_field>": "<table_id from describe_node options>"}},

  {"op": "add_node", "ref": "filt", "type_id": "<find_node('filter rows by condition')>",
   "name": "Filter to target industries", "parents": ["src"],
   "settings": {"<column>": "industry", "<operator>": "in", "<value>": "SaaS,Fintech"}},

  {"op": "add_node", "ref": "enr", "type_id": "<find_node('enrich company')>",
   "name": "Enrich company firmographics", "parents": ["filt"],
   "settings": {"<domain_field>": "{{domain}}"}},

  {"op": "add_node", "ref": "qual", "type_id": "<find_node('ask ai evaluate')>",
   "name": "Qualify against ICP", "parents": ["enr"],
   "settings": {"<model_field>": "<model value from describe_node options>",
                "<prompt_field>": "Company: {{company_name}}, {{employee_count}} employees, {{industry}}. Is this a fit for a 50-500 employee B2B SaaS ICP? Answer fit=yes|no with one-line reason."}},

  {"op": "add_node", "ref": "out", "type_id": "<find_node('add rows to nrev table')>",
   "name": "Write qualified to table", "parents": ["qual"],
   "settings": {"<table_field>": "<destination table_id>"}},

  {"op": "set_test_mode", "all": true, "value": true}
]
```

Then per node needing more config: `update_node_settings`. Test bottom-up with
`run_node(node_id, prior_execution_id=…)`, inspecting `get_node_output` each
time.

---

## 2. Magic Node fan-in — join two streams

Single-input nodes refuse a second `_default` edge. To combine streams, give a
**Magic Node** multiple parents in one `add_node`: the engine wires them to
`df1…df5` and maintains the references setting automatically. Describe the join
in plain English in the Magic Node's prompt.

```json
[
  {"op": "add_node", "ref": "people", "type_id": "<find_node('search people apollo')>",
   "name": "Find contacts", "settings": {"<title_field>": "VP Sales"}},

  {"op": "add_node", "ref": "accounts", "type_id": "<find_node('query nrev table')>",
   "name": "Load target accounts", "settings": {"<table_field>": "<accounts table_id>"}},

  {"op": "add_node", "ref": "join", "type_id": "<MAGIC_NODE id from find_node('transform join merge')>",
   "name": "Match contacts to accounts", "parents": ["people", "accounts"],
   "settings": {"<prompt_field>": "Join df1 (contacts) to df2 (target accounts) on company domain. Keep only contacts whose domain matches an account. Output contact columns plus account_tier."}}
]
```

`parents: ["people", "accounts"]` → `df1` = people, `df2` = accounts (order
follows the parents array). Up to five inputs (`df1`…`df5`).

---

## 3. Listener-triggered — research on a schedule → Slack

A workflow that runs itself. Exactly ONE listener per workflow (the engine
refuses a second). The Scheduler is a start node AND the listener.

```json
[
  {"op": "add_node", "ref": "cron", "type_id": "<SCHEDULER id from find_node('schedule cron trigger')>",
   "name": "Every weekday 9am", "is_listener": true,
   "settings": {"<cron_field>": "0 9 * * 1-5"}},

  {"op": "add_node", "ref": "src", "type_id": "<find_node('query nrev table')>",
   "name": "Load watchlist", "parents": ["cron"],
   "settings": {"<table_field>": "<watchlist table_id>"}},

  {"op": "add_node", "ref": "res", "type_id": "<find_node('ai web research')>",
   "name": "Check for funding news", "parents": ["src"],
   "settings": {"<prompt_field>": "Find any funding round announced in the last 7 days for {{company_name}} ({{domain}}). Return amount, round, date or 'none'."}},

  {"op": "add_node", "ref": "notify", "type_id": "<find_node('send slack message')>",
   "name": "Post hits to #signals", "parents": ["res"],
   "settings": {"<channel_field>": "<channel value from describe_node options>",
                "<text_field>": "{{company_name}} raised {{funding_amount}} ({{funding_round}})"}}
]
```

Notes:
- `is_listener: true` on the root makes the workflow self-firing. A Scheduler
  with `is_listener=false` is a footgun (start node that never fires) and is
  refused.
- Confirm the Slack connection exists first with `list_connections` — an
  app-backed node with no connection fails at run time, not at build time.
- Route a "no news" branch to a table if you want observability on misses.

---

## After building any of these

1. `validate_workflow` — fix every error before running.
2. `run_node` the changed node (with `prior_execution_id` to reuse upstream).
3. `get_node_output` — check the rows AND each row's `error` field; a node can
   report `completed` while every row failed.
4. Final full-mode run only after the user okays the `estimate_run_cost` figure
   (`run_workflow` refuses live runs without `confirm=true`).
