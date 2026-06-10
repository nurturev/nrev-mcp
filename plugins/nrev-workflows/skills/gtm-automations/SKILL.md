---
name: gtm-automations
description: Use when the user wants actions executed on entities — "update the CRM", "log to a sheet", "send connection requests", "notify on Slack" — or reactive triggers ("when a bounce happens", "when they reply"), rate-limited queues ("send 15 per day"), or results delivered back to a Slack thread. Covers system writes, event-driven listeners, queue-based drip processing, and interactive response delivery.
---

# GTM Automations

GTM automations take action on entities — writing records, sending messages, executing LinkedIn actions, managing queues, reacting to events. This is the terminal execution layer downstream of list building, research, qualification, and nomination. It answers: **what do I do with these entities now?**

Invoke when the prompt describes: an action to execute ("send", "update", "log", "notify", "create a task"); a reactive trigger ("when X happens"); a queue with rate constraints ("15 per day", "drip over time"); state tracking across runs ("don't send twice", "only process new entries"); or delivering results back to a requester ("respond in the thread").

Identify which sub-pattern(s) apply — one workflow may combine several. Confirm the plan with the user before building when destructive or external-facing actions (CRM writes, sends) are involved.

**Translate casual language into state machinery:**
1. "Don't send twice" → anti-join dedup. "Keep updated" → upsert. "Only new ones" → delta via left join + filter. Every "don't re-do" implies a completion log PLUS a join against it each run — today's output becomes tomorrow's exclusion input.
2. **Normalise identifiers on BOTH sides of EVERY join** — otherwise joins fail silently (re-processing or missed matches).
3. **Audit-first for listeners** — log before routing.
4. **Parallel side effects** for multi-system updates — fan out from If/Else; never chain independent actions.

## 1. System Writes (CRM, Sheets, databases)

Trigger phrases: "update the CRM", "create a contact", "log to a sheet", "push to Salesforce/HubSpot".

```
[Optional: Read existing state] → [Parse/Extract payload] → [Optional: Split Data]
  → [Optional: Filter/dedupe] → [Optional: Compute via Ask AI] → [Optional: Transform via Magic Node]
  → [Write] → [Optional: Chained write using previous write's output ID]
```

| Decision point | Configuration |
|---------------|---------------|
| Check existing state first? | **Read-before-write:** query target → check for existing record → conditionally write. Use for dedup-sensitive creates; skip for plain Sheets appends. Example: SOQL `SELECT Id, Email, OwnerId, (SELECT Id FROM Tasks) FROM Lead WHERE Email = '{{Email}}'` → extract → Filter `tasks is_empty` → Create Task. |
| Record may already exist? | **Upsert:** `updateIfExists: true` (HubSpot) or check-then-create/update — for recurring runs. |
| Related records? | **Chained writes:** first write returns `payload_2.id` → second write uses it as the foreign key. Strictly sequential — never parallelise. |
| AI output needs type conversion? | **Post-AI transformation in a Magic Node** (Custom Code only as last resort): `pd.to_numeric(df['score'], errors='coerce')`, epoch-ms timestamps `int(x.timestamp() * 1000)`, `df.explode('events')` + per-element field extraction. |

Practices:
- **Payload parsing:** integration nodes return raw JSON in `payload_N` columns (numbered by execution order). Create Column for simple nested access (`"Results" = {{payload.results}}`); a Magic Node for multi-field/`json.loads()` parsing. Then reference flat columns as `{{column_name}}`.
- **Read-compute-writeback** (always writes, unlike read-before-write): Search CRM fetching extra properties → Split Data → Ask AI scores using rules + existing CRM state → Magic Node type conversions → Create or Update Contact upsert.
- **Field mapping:** populate only required fields, context fields from the read step (OwnerId, WhoId), and content fields from upstream. Ignore optional fields unless asked.
- **System notes:** Salesforce — SOQL subqueries for related objects; junction objects (TaskRelation, OpportunityContactRole) need IDs from both sides; ISO 8601 timestamps. HubSpot — `additionalProperties` on Search CRM; epoch milliseconds; Create Engagement with numeric `associationType`. Google Sheets — usually plain append; format columns upstream to match the sheet (see node-settings skill for the canonical Sheets CRUD patterns and field traps).

## 2. Event-Driven Listeners

Trigger phrases: "when X happens", "react to", "handle bounces/unsubs", "trigger on form submission".

```
[Listener — webhook, CRM event, Slack message]
  → [Normalise — Create Column flattens payload to standard columns]
  → [Audit Log — Google Sheets append BEFORE any routing]
  → [Route — If/Else on event type]
      ├── [Action A]  ├── [Action B]  └── [else path]
```

| Decision point | Configuration |
|---------------|---------------|
| Event source | Webhook (outreach tools like Instantly), CRM trigger, Slack message, form submission. Listener node has `isTrigger: true`, `isListener: true` (one listener max per workflow — see node-settings skill). |
| Normalisation | **Always.** First node after the listener: Create Column extracting `"Contact" = {{data.email}}`, `"Event" = {{data.event_type}}`. Downstream references `{{Contact}}`/`{{Event}}` regardless of source; a source change only touches this mapping. |
| Log before routing | **Always.** Audit-first append of the raw event — if downstream fails, the event is still recorded; the log doubles as a debugging tool. |
| Routing | **If/Else, not Filter** — preserves all rows on different paths. TRUE: negative events (bounce, unsub, wrong-person → removal + CRM DQ update). ELSE: positive events (reply, click → enrichment, score update). |
| Multi-system actions | **Parallel paths from If/Else** — e.g. remove from campaign (API) AND update CRM with DQ reason. Independent; one failure doesn't block the other. |
| No native integration node | **Magic Node with `requests`** (Custom Code last resort): handle pagination, `time.sleep()` rate limiting, guard `if len(df) > 0` (listeners can fire with empty test payloads), store responses in columns. |
| Identifier mismatch | **Cross-system resolution:** webhook gives email, CRM needs contact ID → Search CRM → extract ID (explode for multi-match) → update. |

Slack listeners: `ignoreBot = true` (prevent infinite self-reply loops), `ignoreThreads = true` (top-level messages only).

Common shapes: negative event → multi-system cleanup; positive event → enrichment + score update + sales notification; CRM stage change → downstream sync; Slack message → research → respond.

## 3. Queue-Based Drip Processing

Trigger phrases: "send X per day", "process the queue", "drip campaign", "don't re-send".

```
[Scheduler] → [Read Queue sheet] → [LEFT JOIN with Completion Log] → [Filter: is_done is_empty]
  → [Limit: N per Owner, sorted by Priority] → [Optional warm-up] → [Execute action]
  → [Filter: status_1 == "success"] → [Log to Completed sheet] → [Optional If/Else post-routing]
  (+ parallel reconciliation path)
```

| Decision point | Configuration |
|---------------|---------------|
| Queue source | Google Sheets (or an nRev table) as input queue, state store, and audit log. Sheets are human-editable: sales can add/reorder/reprioritise via the `Priority` column. Three worksheets: Input, Completed, Already Connected (reconciliation). |
| Deduplication | **Anti-join:** LEFT JOIN Input with Completed on the FULL work-item key (`linkedin_url + Owner + Campaign + Source + Insight` — not just the person; the same person can appear in multiple campaigns), then Filter `is_done is_empty`. |
| URL normalisation | **Always, BOTH sides of every join.** In a Magic Node: `df['linkedin_url'] = df['linkedin_url'].str.replace('http://', 'https://').str.rstrip('/')`. Generalises: email (lowercase, trim), domain (strip www), phone (strip formatting). |
| Rate limiting | **Limit node:** `limit_across_groups: true`, `grouping_keys: ["Owner"]`, `limit: N`, `column_to_sort: "Priority"`, `sorting_order: descending`. N = daily cap ÷ runs per day, with headroom (LinkedIn ~20-25/day → use 15). |
| After the action | **Status gate:** Filter `status_1 == "success"` BEFORE logging — failures stay in the queue and retry next run; logging failures as completions kills retries. Add `today_date` in a Magic Node (`dt.date.today().strftime('%Y-%m-%d')`). |
| Post-action routing | **If/Else after logging:** e.g. `connected == true` → write to Already Connected immediately rather than waiting for reconciliation. |
| Reconciliation | **Parallel path checking external ground truth:** Completed → Group by Owner → fetch LinkedIn connections → standardise URLs both sides → joins to detect new acceptances → log. Eventual consistency — async state changes get caught next run. |
| Multi-owner | `Owner` column drives the acting account (`edges_connection_id: {{Owner}}`), per-owner limits, per-owner reconciliation. |

Warm-up (LinkedIn-specific, optional): Visit Profile + Like Post (random reaction, posts from last ~5 months, non-reposts, one per person+owner) in parallel before the main action; fire-and-forget — never gates the send. Skip for non-social actions.

Applicability: LinkedIn connections (15-25/day/account), daily enrichment batches (API quotas), email warm-up (50/day/mailbox), LinkedIn engagement (30/day/account), content publishing (3/day/channel).

## 4. Interactive Response Delivery

Trigger phrases: "respond in the thread", "send the results back".

- **Payload preservation via fork-and-rejoin:** fork the listener output to a Merge node early; run the pipeline on the main path; join back on entity key at the end to recover `{{payload.channel}}` and `{{payload.ts}}`.
- **Thread reply settings:** `conversation = {{payload.channel}}`, `thread_ts = {{payload.ts}}`, `mrkdwn = true`, custom bot identity (`username` + `icon_emoji`), `unfurl_links = false`.
- **Multi-message responses** (score summary, then detailed brief): a separate fork-and-rejoin with its own Merge node per message.

## Content Generation as a Component

The flow research → qualify → nominate → generate → send embeds the template-driven content pattern (see the content-generation skill): high-quality models for generation, all pieces in one call for consistency, self-evaluation fields in the schema, maker-checker verification, templates in Google Sheets for non-technical editing, conditional rules via upstream flag variables.

## Post-Action Gating

After any action that returns a status, Filter on `status == "success"` before logging — Path A mechanics where the "classification" is the action's return status. Failed actions remain queued for retry.

## Build and Verify

Find integration nodes with `search_nodes`; fetch settings schemas with `get_node_type` and connection/dropdown values with `list_connections` and `get_field_options` — never fabricate field names (Pipedream connection fields are inconsistently named; see node-settings skill). Build with `edit_workflow` (add_node/add_edge), configure with `update_node_settings`. Test write nodes with `run_node` on one row first, then check `get_node_output` for ROW-LEVEL errors — Pipedream and nrev_tables nodes can report block-level success while every row failed. Use `run_workflow` + `get_execution` for end-to-end runs.

## Boundaries

GTM automations execute. Assembling lists is **List Building**; gathering intelligence is **Research**; fit determination is **Qualification**; selecting entities is **Nomination**. Automations frequently trigger other operations (Slack bot → on-demand research; enrichment results → re-score) — the operations are composable, not strictly sequential.
