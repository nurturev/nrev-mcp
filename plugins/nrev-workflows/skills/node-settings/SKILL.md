---
name: node-settings
description: Use whenever writing or editing the settings of any nRev workflow node — adding nodes via edit_workflow, template syntax, native-node settings shapes (LinkedIn Scraping, People/Company Data, nRev Tables, Ask AI), Pipedream field naming and Google Sheets CRUD, structured output, and model selection. Consult BEFORE configuring a node; the shapes here are verified against production.
---

# Node Settings

## Discipline

- **Never fabricate node settings, field names, or typeIds.** Find nodes with `search_nodes`, fetch the live schema with `get_node_type` (field names, required flags, dropdown options, conditional visibility, defaults), and resolve dropdown/dynamic values with `get_field_options`. The shapes below are production-verified — copy, edit values, pass as `settings` when adding the node via `edit_workflow` (add_node), or apply to an existing node with `update_node_settings`.
- The catalog `value` slug (e.g. `company_data.enrich_company`) names the node family; that family is the **prefix** before the first hyphen in every settings field for that node.
- If a node isn't covered here or by `get_node_type`, read a working example: `get_workflow` on an existing workflow that uses the typeId and copy its settings shape.
- Verify every configured node: `run_node` with limited data, then inspect `get_node_output`.

## Template syntax (universal)

- Bare `{{column_name}}` resolves to the upstream column value. NOT `{{data.column_name}}` — the `data.` prefix works for some Pipedream/Slack actions only, never for native or nrev_tables nodes.
- Column names MUST be valid Python identifiers (snake_case, no spaces or hyphens). `{{Linkedin URL}}` silently fails to resolve — rename the upstream column to `linkedin_url`.
- **Templates always resolve to STRINGS.** Number/boolean target columns reject with "Cell type mismatch: expected number, got str". Cast upstream in a Magic Node: `df["score"] = df["score"].astype(int)`. Text columns work fine with raw templates.
- **Templates are validated at attach time** against the parent's declared output columns. `"Fields not found in available data: <name>"` means the parent hasn't declared that column yet. Fixes: run the parent once with `run_node` so it declares its outputs (Search People, Get Person Profile, and most LinkedIn Scraping nodes declare columns only after first execution), or declare `output_columns` explicitly when adding a Magic Node — without it, downstream nodes keep seeing UPSTREAM columns and your transform's columns are invisible.

## Two settings shapes for native nodes

Picking the wrong shape returns `"Whoops! Missing a field - <X>"`.

**Shape A — flat** (single-input actions: one linkedin_url, one domain):

```python
settings = {"<prefix>-<field>": "<value>"}
```

**Shape B — reference-group envelope** (actions that disambiguate input methods: domain OR linkedin OR name; group usually named `<entity>_reference`):

```python
settings = {"<prefix>-<group_field>": [
    {"field_name": "<FULL-PREFIX inner field>", "field_value": "<value>"},
]}
```

**Inner `field_name` carries the FULL `<app>-<action>-<key>` prefix** — not the bare key. Bare-key form (`"linkedin_url"`) fails with "Whoops! Missing a field". Applies to all Shape-B nodes (Enrich People, Search People, Enrich Company, Fetch Jobs).

## Verified native node shapes

| Node | typeId | Shape | Root-capable? |
|---|---|---|---|
| Get Person Profile (LinkedIn Scraping) | `4e5005c4-b1a5-417b-af59-453b86f489db` | A | NO — needs a parent supplying `linkedin_url` |
| Get Post by Person (LinkedIn Scraping) | `c854f6d7-f44d-470f-8e9c-f3c42a24a888` | A | YES |
| Enrich People (People Data) | `6439527f-abe7-44e5-b462-60e1a45be619` | B (`person_reference`) | NO |
| RocketReach: Enrich People | `43ae6689-b0f2-44bc-b34a-970ec02dedd2` | B (`person_reference`) | YES |
| Search People — Apollo (People Data) | `15145759-901a-4a87-8db3-84cd9e734a49` | B (`person_reference`) | YES |
| RocketReach: Search People | `99631757-7b8a-4fc9-9733-b47d5702d9b2` | B (different filter fields) | YES |
| Enrich Company (Company Data) | `1e908fa8-d63b-4a67-bb58-004dc15052e2` | B (`company_reference`) | NO |
| RocketReach: Enrich Company | `119be39f-278e-46fd-a0b9-15bc81eb85cb` | **FLAT with `lookup_by` discriminator** | YES |
| Fetch Jobs (Company Data) | `d78f7f27-3759-4590-a6a7-525dbda774b1` | B (group named `company_details`, NOT `company_reference`) | NO |
| Ask AI (AI Toolkit) | `78dc33d4-c4d5-433a-8e65-c549faca037c` | flat fields | — |

Catalog category gotcha: the LinkedIn family is `Linkedin Scraping` (lowercase k) — searching category "LinkedIn Scraping" returns 0 results.

```python
# Get Person Profile — canonical one-off: Search People (root) → Get Person Profile,
# or CSV/Sheets read supplying linkedin_url → Get Person Profile
{"linkedin_scraping-get_person_profile-linkedin_url": "{{linkedin_url}}"}

# Enrich People — reference envelope + enrichment field selection
{"people_data-enrich_people-person_reference": [
    {"field_name": "people_data-enrich_people-linkedin_url", "field_value": "{{linkedin_url}}"},
    # OR (mutually exclusive): ...-email / ...-name
 ],
 "people_data-enrich_people-enrichment_fields": [
    "linkedin_url", "employment_history", "title", "seniority", "functions",
    "name", "org_name", "org_primary_domain", "org_short_description",
    "headline", "org_estimated_num_employees", "org_keywords",
 ]}
# Allowed enrichment_fields: exactly the 12 values above. Output columns = your
# subset; order matters for downstream mapping — keep the list short.

# Search People (Apollo) — at least one real criterion; name alone returns
# "At least one search criteria field must be provided"
{"people_data-search_people-person_reference": [
    {"field_name": "people_data-search_people-name", "field_value": "Alice Example"},
    {"field_name": "people_data-search_people-organization_name", "field_value": "Acme Corp"},
 ],
 "people_data-search_people-per_page": 1}   # cap to control cost

# Enrich Company (native)
{"company_data-enrich_company-company_reference": [
    {"field_name": "company_data-enrich_company-domain", "field_value": "{{company_domain}}"},
    # OR ...-linkedin_url / ...-name (mutually exclusive)
 ]}

# RocketReach: Enrich Company — DO NOT copy the native envelope; flat + discriminator
{"company_data-rocketreach_enrich_company-lookup_by": "company_domain",
 "company_data-rocketreach_enrich_company-company_domain": "{{company_domain}}"}
# lookup_by="company_linkedin_url" → set ...-company_linkedin_url instead;
# lookup_by="company_name" → set ...-company_name.

# Fetch Jobs — envelope named company_details
{"company_data-fetch_jobs-company_details": [
    {"field_name": "company_data-fetch_jobs-domain", "field_value": "{{company_domain}}"},
 ]}
```

## nRev Tables nodes

Four typeIds, prefix `nrev_tables-<action>-*`:

| Node | typeId | Role |
|---|---|---|
| Query Table | `a1b2c3d4-0003-4000-8000-000000000003` | TRIGGER, read |
| Add Row | `a1b2c3d4-0001-4000-8000-000000000001` | action, write |
| Update Row | `a1b2c3d4-0002-4000-8000-000000000002` | action, upsert |
| Get Row | `a1b2c3d4-0004-4000-8000-000000000004` | TRIGGER, single row |

```python
# Query Table — limit MUST be a STRING from the enum "100"/"500"/"1000"/"5000"/"10000"/"50000"/"100000"
{"nrev_tables-query_table-table_id": "<table_uuid>",
 "nrev_tables-query_table-limit": "100",
 "nrev_tables-query_table-filter_operator": "AND",
 "nrev_tables-query_table-filters": [{"column_id": "<col_uuid>", "operator": "gt", "value": "0"}],
 "nrev_tables-query_table-sort_column": "<col_uuid>",     # optional
 "nrev_tables-query_table-sort_direction": "desc"}        # asc | desc

# Add Row — LIST-OF-LISTS-OF-ENVELOPES, NOT a flat dict.
# The short [{"column_id": x, "value": y}] form fails with "Invalid Add Row settings".
{"nrev_tables-add_row-table_id": "<table_uuid>",
 "nrev_tables-add_row-column_values": [
    [  # inner list per column: exactly TWO envelopes (column_id + value)
      {"field_name": "column_id", "field_value": "<col_uuid>", "fieldLabel": "name"},
      {"field_name": "value", "field_value": "{{name}}"},
    ],
 ]}

# Update Row — same envelope style with match_conditions + fields_to_update
{"nrev_tables-update_row-table_id": "<table_uuid>",
 "nrev_tables-update_row-match_conditions": [[
      {"field_name": "column_id", "field_value": "<col_uuid>", "fieldLabel": "email"},
      {"field_name": "operator", "field_value": "eq"},
      {"field_name": "value", "field_value": "{{email}}"},
 ]],
 "nrev_tables-update_row-fields_to_update": [[
      {"field_name": "column_id", "field_value": "<col_uuid>", "fieldLabel": "status"},
      {"field_name": "value", "field_value": "replied"},
 ]],
 "nrev_tables-update_row-add_row_if_not_found": False}   # bool: upsert vs strict update
# Get Row: table_id + the same match_conditions envelope; returns a single row.
```

- **Column UUID discovery:** after attaching with just `table_id`, call `get_field_options` — it returns the table's columns as `{label: "<column_name>", value: "<column_uuid>"}`. One round-trip.
- **Type coercion:** templates are strings — cast number/boolean values in an upstream Magic Node before Add/Update Row.
- **Silent row-level errors:** add_row/update_row report `status: completed, error: null` at block level even when EVERY row failed the cell type check — failures live in `row[i].error`. Always inspect `get_node_output` after a run.

## Ask AI — structured output, models, web research

Two-step structured output config (each top-level schema key becomes an output column):

```python
update_node_settings(..., "ai_toolkit-ask_ai-response_type", "structured_output")
update_node_settings(..., "ai_toolkit-ask_ai-response_json",
    '{"Fit": "true or false", "persona_bucket": "AI/ML | Eng Leadership | None", "reason": "1-2 sentences"}')
```

The platform stores `response_json` as a **JSON-formatted TEXT STRING**, not a parsed dict — the UI's Structured Output editor renders raw text, and a dict-stored value shows a **blank editor box** even though the runtime still works. Pass the schema as a JSON string; re-saving the field fixes an already-blank node.

`ai_toolkit-ask_ai-model` values (verified live; default `gpt-4.1`; per-row cost in credits):

| Provider | Values (cost) |
|---|---|
| OpenAI | `gpt-5.4` (8), `gpt-5.4-mini` (3), `gpt-5.2` (5), `gpt-5.1` (4), `gpt-5` (4), `gpt-5-mini` (2), `gpt-5-nano` (1), `gpt-4.1` (2), `gpt-4.1-mini` (1) |
| OpenAI o-series | `o3` (3), `o4-mini` (3) |
| Parallel Web | `lite` (2), `base` (3), `core-fast` (5) |
| Claude | `CLAUDE_OPUS_4_7_INFERENCE_PROFILE_URN` (8), `CLAUDE_OPUS_4_6_INFERENCE_PROFILE_URN` (8), `CLAUDE_SONNET_4_6_INFERENCE_PROFILE_URN` (5), `CLAUDE_HAIKU_4_5_INFERENCE_PROFILE_URN` (2) |

Claude values are the full URN, not a slug. **`web_search_enabled` (and `prompt_file_urls`) is OpenAI-only** — with a Claude model the runtime silently ignores it (invalid config, no error; don't ship it). **Parallel Web models have web research BAKED IN** — the toggle is meaningless for them. Decision tree:
- Web research baked in → Parallel Web model (cheapest: `lite` @ 2)
- GPT model + web research → OpenAI model + `web_search_enabled=true` (+3 credits/item)
- Claude model + web research → not directly supported; pipe an upstream research step (OpenAI+web_search Ask AI or a scrape node) into a Claude Ask AI for synthesis
- Claude, no web → pick the Claude model, leave the toggle unset

## Graph and wiring rules

- **Almost every node is single-input.** Never wire multiple `_default` edges into a single-input node — it looks fine in the UI and silently breaks at execution. For joins/merges use a **Magic Node** (1-5 inputs, handles `df1..dfN`); the legacy Merge block is the only legitimate multi-input non-Magic node.
- **Prefer Magic Node for all code/data transformation.** Custom Code is a last resort.
- **isTrigger = start node** (swimlane entry; at least one required, multiple allowed). **isListener = live polling automation trigger** (Scheduler, Gmail New Message, Sheets Read are listener-capable) — **max ONE listener per workflow**. A listener-capable root attached when a listener already exists must be demoted (`is_listener=False`) for a one-off read.
- Action-only nodes (Magic Node, Custom Code, and every node marked NO in the root-capable column above) cannot be workflow roots.
- Validation checklist before running: no orphan non-trigger nodes, no handle mismatches on edges, exactly one listener, all required settings present (`get_workflow` shows current state).

## Pipedream nodes

**Connection-field naming is inconsistent and NOT inferable** — never guess from a formula. Examples:
- Gmail Send Email → `pipedream-gmail-gmail_send_email-gmail`
- Slack V2 Send Message → `pipedream-slack_v2-slack_v2_send_message-slack_v2`
- Slack New Message channel → `pipedream-slack_v2-slack_v2_new_message_in_channels-conversations` (NOT `channel`/`channelId`)
- Sheets Add Single Row → `pipedream-google_sheets-google_sheets_add_single_row-googleSheets_connection_id`

The only reliable way: add the node with the connection field first (`list_connections` for the id), then call `get_field_options` to discover the exact remaining field names. A freshly added Pipedream node exposes only its connection field — the action's full field list materialises after initial settings are submitted.

**Google Sheets traps:**
- `sheetId` = the spreadsheet's URL ID (the long string after `/d/`), NOT the workbook name. `get_field_options` lists accessible spreadsheets as `{label: name, value: id}`.
- `worksheetId` = the tab's numeric `gid` (after `#gid=` in the URL), NOT the tab name ("Sheet1"/"Leads").

```python
# Canonical Get Values in Range (READ)
{"pipedream-google_sheets-google_sheets_get_values_in_range-googleSheets_connection_id": "<connection_id>",
 "pipedream-google_sheets-google_sheets_get_values_in_range-drive": "My Drive",
 "pipedream-google_sheets-google_sheets_get_values_in_range-sheetId": "<spreadsheet URL ID>",
 "pipedream-google_sheets-google_sheets_get_values_in_range-worksheetId": "<numeric tab gid>",
 "pipedream-google_sheets-google_sheets_get_values_in_range-range": "A1:E100"}  # A1 notation; required
```

**Canonical Sheets CRUD:** READ with Get Values in Range (platform reads the header row, emits one row per sheet row). WRITE one row at a time with **Add Single Row** (typeId `191db4a1-7c72-4c4a-af02-b507701ca61b`) in a per-row loop — NEVER Add Multiple Rows. Static fields: connection, drive, sheetId, worksheetId, hasHeaders (`hasHeaders=true` → upstream column names match sheet headers; `false` → upstream column order maps to A,B,C...). DO NOT set `myColumnData` in settings — it persists silently and is ignored at runtime. UPDATE/DELETE follow the same single-row pattern with row identity from upstream.

**Two-phase column mapping (Add Single Row / Add Multiple Rows / Upsert Row / Update-Upsert Row):** after the static fields bind, the platform expands per-column fields `col_0000`, `col_0001`, ... — one per destination header. Until these exist AND are mapped, the node looks configured but writes empty rows. Discover the `col_NNNN → header` mapping with `get_field_options`, then map each explicitly with `update_node_settings` (e.g. `col_0000 = "{{first_name}} {{last_name}}"`, `col_0001 = "{{personal_email}}"`). Do not rely on name-based auto-matching — when destination headers don't match upstream column names verbatim it silently writes broken templates.

**Row-level error detection:** a Pipedream node "completing" with `error: null` does NOT mean the action succeeded — the real error may sit in `row[0].error` of the output. After every Pipedream or nrev_tables write, check `get_node_output` for row-level errors.

**Cross-tenant connections:** Gmail and Sheets accept another user's connection_id at runtime; Google Calendar throws `Cannot read properties of undefined (reading 'oauth_access_token')`. In multi-user tenants, have each user OAuth their own connection.

## Test data and verification loop

- Test a single node with `run_node` (limited rows); inspect via `get_node_output`; use `download_node_output` for large datasets; `run_workflow` + `get_execution` for end-to-end runs with per-node status.
- For seed/test input data: create an nRev table (`create_table` + `add_table_rows`) and read it with an nrev_tables Query Table node as the workflow root — or ask the user to upload a CSV in the platform UI.
- Build incrementally: add a node, configure, `run_node`, confirm output columns, then attach the next — attach-time template validation (above) makes this ordering materially cheaper than building the whole graph blind.
