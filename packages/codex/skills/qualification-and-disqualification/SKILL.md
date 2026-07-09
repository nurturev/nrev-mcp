---
name: qualification-and-disqualification
description: Use when the user wants entities evaluated against criteria for a fit/unfit determination — "score these leads", "qualify against our ICP", "only companies that match", "filter to the good ones" — or removed on negative signals ("if they bounce", "when someone unsubscribes"). Covers binary and score-based AI evaluation, deterministic pre-filtering, multi-signal scoring, and event-driven disqualification.
---

# Qualification and Disqualification

Qualification answers: **is this entity worth pursuing?** Disqualification answers: **should this entity be excluded?** Same mechanism, different intent:

| Aspect | Qualification | Disqualification |
|--------|--------------|------------------|
| Default assumption | Unqualified until proven | Qualified until flagged |
| Filter direction | Keep rows that pass | Remove rows that fail |
| Typical use | Narrowing a broad pool | Cleaning a list, handling bounces/unsubs |
| Example filter | `score >= 60` | `bounce_reason is_empty` |
| Where it appears | After list building or research | Event-driven listeners reacting to negative signals |

Invoke **qualification** when: fit criteria can't be resolved by search filters alone (business model, composite ICP fit, pricing model need AI judgment); the user wants a score or label before acting; the prompt implies narrowing between a broad list and targeted action. Invoke **disqualification** when: negative events should trigger removal; the user wants an existing list cleaned; a webhook carries a negative signal (bounce, unsubscribe, wrong-person, spam complaint).

If the user references "our ICP" or a scoring rubric without supplying it, ask them for the definition before building the qualification prompt.

## Decomposition Checklist

### Step 1 — Enumerate every criterion and pick its resolution method

| Criterion type | Resolution method | Pattern |
|---------------|-------------------|---------|
| Numeric threshold (headcount > 100) | Deterministic filter — no AI | Filter node, comparison operators |
| Category match (seniority in [Director, VP, C-Suite]) | Deterministic filter | Filter node, in/equals |
| String exclusion (domain does_not_contain ".vc") | Deterministic filter | Filter node, does_not_contain |
| Persona classification (strategic buyer vs operational manager) | AI classification, no web search | Path A: Classifier on title + headline → Filter on label |
| Business model (SaaS? PLG? marketplace?) | AI + web research on the domain | Path A: Ask AI with web research → Filter |
| Composite ICP fit (multiple firmographic signals weighed together) | AI + web research + ICP rubric | Path A: Ask AI with web research + rubric |
| Multi-signal scoring (aggregate research events) | AI scoring on grouped data | Path B: Group by entity → Ask AI with scoring framework (gpt-5.2) |

### Step 2 — Binary or scored?

| Mode | When | Output schema | Downstream |
|------|------|---------------|-----------|
| **Binary (yes/no)** | Clear-cut fit criteria | `{"qualified": "yes/no", "reason": "..."}` | Filter: `qualified == "yes"` |
| **Score-based (0-100)** | Degrees of match, or downstream ranking needed | `{"score": 75, "rationale": "...", "section_wise_scoring": "..."}` | Filter: `score >= threshold` (threshold as a workflow variable for tunability) |

**Always include `reason`/`rationale` in the output schema.** Near-zero cost; invaluable for debugging, audit, and downstream content. Forcing the AI to defend its assessment is the maker-checker principle applied to qualification.

### Step 3 — Path A or Path B?

| Decision point | Path A (Individual Classify + Filter) | Path B (Group Synthesise + Score) |
|---------------|---------------------------------------|----------------------------------|
| Input structure | One row per entity, evaluated independently | Multiple signal rows per entity to aggregate |
| AI interaction | Classifier or Ask AI per row | Ask AI on grouped data per entity |
| Use when | Binary qualification, persona classification, business model check | Multi-signal scoring after research (hiring + tech + posts → score) |
| Model | Mini for simple classification; core-fast/gpt-5-mini for web-research qualification | gpt-5.2 for nuanced multi-signal reasoning |
| Grouping needed? | No | Yes — Group Data by entity key, `unique` aggregation |

### Step 4 — Deterministic pre-filtering before AI

Reduce AI cost with cheap filters first: domain validity (`org_primary_domain contains "."`), category exclusions (`industry does_not_contain "venture capital"`, `domain does_not_contain ".vc"`), numeric thresholds (`employee_count >= 50`, `founded_year > 2015`). If N is large (500+), put deterministic filters BEFORE the AI; if moderate (50-100), filter after for simplicity.

### Step 5 — Is qualification embedded in another operation?

| Scenario | Where qualification sits | Pattern |
|----------|------------------------|---------|
| People at companies matching a non-searchable criterion | Inside list building (Route 2a branch) | Search People → fork → Group companies → AI qualify → Filter → inner join back |
| Scored list based on research signals | After research | Swimlanes → event convergence → Group by entity → AI score with framework |
| Raw list provided, filter for fit | Standalone | Read list → Path A classify/score → Filter |
| Event-based removal | Inside GTM automations | Webhook → normalise → route → disqualify + multi-system cleanup |

Embedded qualification must still be decomposed explicitly — it is the most common omission.

## Web Search — The Critical Decision

- **Without web search:** the AI reasons only from data in the row (title, headline, description, enrichment fields). Sufficient for persona classification, label assignment.
- **With web research:** the AI looks up external information (website, funding, news). Required whenever the criterion depends on information NOT in the row — business model, pricing, competitive positioning, composite ICP fit.

**Rule:** if the criterion needs knowledge the dataset doesn't contain, web research is required. Ask AI without web research for business-model or ICP questions produces hallucinated assessments. (Model constraint: `web_search_enabled` applies only to OpenAI models; Parallel Web models like core-fast have web research built in — see the node-settings skill.)

## Path A — Binary and Score-Based Qualification

```
Ask AI / Classifier: entity fields → {"qualified": true/false, "reason": "..."}
    ↓
Filter: qualified is_true          (or: score >= threshold)
```

- Feed minimum fields for a confident call. Companies: `name`, `domain`, `industry`, `headcount`, `description`. People: `title`, `headline`, `seniority`.
- In binary mode the catch-all is the `false` case; in category mode include "Others" to prevent forced false positives.
- Cast a wider net at search time; qualification is where precision happens.
- Layer deterministic filters on the AI output: `score >= 60 AND seniority in [Director, VP, C-Suite] AND company_size >= 50`.
- For score mode, define score bands: high-intent 70-100, moderate 40-70, weak 20-40, no signal 0-30. Make the threshold a workflow variable (`<<wf_var.uuid>>`, created via `manage_variables`) so it can be tuned without editing the workflow.
- Enrich broadly before qualification — missing context produces generic or wrong assessments.

## Path B — Group-Level Multi-Signal Scoring

When the AI needs all signals per entity to produce a holistic score (e.g. scoring a company from its hiring pipeline + tech stack + LinkedIn activity):

```
[Research events] → Group by entity key (domain_name), aggregate unique events → "Unique Events"
    ↓
Ask AI "Score and Synthesise" (gpt-5.2):
    Input: {{Unique Events}} for {{domain_name}}
    Scoring framework: <<wf_var.scoring_framework_uuid>>
    Output: {"overall_score": "0-100", "section_wise_scoring": "...",
             "rationale_for_scoring": "...", "slack_summary": "..."}
```

1. **Compress before AI, always** — grouping converts O(N) AI calls to O(1) per entity.
2. **`unique` aggregation** — repeated events bias the score.
3. **Grouping key = the entity being scored.**
4. **Premium model for scoring** (gpt-5.2) — multi-signal judgment is user-facing, unlike extraction where mini models suffice.
5. **Inject the scoring framework via workflow variable** (event categories, signal weights, score-range meanings) so non-technical users can adjust criteria. Manage with `manage_variables`.
6. **Fallback messaging for low data:** "If ≤2 events: 'Unfortunately there is very little data available...'" — never return empty/confusing results.
7. **Output both machine-readable** (`overall_score` for filtering/sorting) **and human-readable** fields (`section_wise_scoring`, `rationale_for_scoring`, `slack_summary`).

The pattern chain: parallel multi-signal research → convergence → Path B scoring → Filter. The richer the research, the more accurate the qualification. When structured justification is needed, bake it into the schema: per-section scores plus explicit verification fields (`"case_study_verified": "yes"`, `"no_hallucinated_data": "true"`).

## Disqualification in Event-Driven Workflows

```
[Webhook Listener — e.g. Instantly event]
    ↓
[Normalise — Create Column: Contact, Event, campaign_id]
    ↓
[Audit Log — Google Sheets append]
    ↓
[If/Else: Event in (email_bounced, lead_unsubscribed, lead_wrong_person)]
    ↓ TRUE
    ├── Remove lead from campaign (API call via Magic Node)
    └── Update CRM contact with DQ reason (HubSpot)
```

1. **Normalise the payload first.** Create Column extracts standard fields (`Contact`, `Event`) so downstream nodes reference `{{Contact}}`/`{{Event}}` regardless of source.
2. **Audit-first logging.** Log the raw event BEFORE routing — if downstream fails, the event is still recorded.
3. **If/Else for routing, not Filter.** If/Else preserves all rows: TRUE → disqualification actions; ELSE → positive events (enrichment, engagement).
4. **Parallel multi-system side effects.** One DQ event often updates several systems (remove from campaign + CRM update); each path is independent.
5. **Direct API calls via a Magic Node** with `requests` for systems without native integration nodes — handle pagination, rate-limit with `time.sleep()`, guard with `if len(df) > 0`. Use Custom Code only as a last resort.
6. **Cross-system entity resolution.** Webhook identifies by email; CRM updates need the contact ID — Search CRM + a Magic Node to resolve identifiers before updating.

## Build Discipline

Find evaluation nodes with `search_nodes`; fetch live settings schemas with `get_node_type` and dropdown values with `get_field_options` — never fabricate settings. Build with `edit_workflow` (add_node/add_edge), configure with `update_node_settings`, test the AI step on a small slice with `run_node`, and read the labels/reasons in `get_node_output` to sanity-check the rubric before running the full list.

## Boundaries

Qualification ends when entities carry a fit determination (score, label, or flag). Assembling the initial list is **List Building**; gathering the intelligence used for scoring is **Research**; selecting specific entities from the qualified pool is **Nomination**; acting on them is **GTM Automations**.
