---
name: nomination
description: Use when the user wants specific entities selected from a qualified pool for action — "pick the best", "top 10", "who should I contact first", "the right person at each company", or recurring batch selection under capacity constraints ("we can only handle 50 this week"). Covers score-and-rank, persona gates, best-per-group comparative selection, and priority queues.
---

# Nomination

Nomination narrows a qualified list down to the specific entities that will receive action — the bridge between "worth considering" (Qualification) and "act on these" (GTM Automations). It answers: **which specific entities should I act on, and in what order?**

| Aspect | Qualification | Nomination |
|--------|--------------|------------|
| Question | Is this entity worth pursuing? | Which specific entities do I pursue? |
| Output | Score/label on EVERY entity | A subset selected for action |
| Volume reduction | Moderate | Aggressive (top N, best per group) |
| Mechanism | Path A classify + filter (broad) | Path A (selective), Path B comparative, or Limit + sort |

Distinguishing examples: "Which companies match our ICP?" / "Score these leads 0-100" → Qualification (every entity gets a determination). "Which should we target first?" / "Top 20 by score" / "Find the right person to contact at each company" → Nomination (a subset survives). They often run as sequential filter stages in one workflow, but qualification runs on ALL entities while nomination applies a more aggressive, often comparative, selection on the qualified pool.

Invoke nomination when the user wants: a subset picked from a larger pool; prioritisation under capacity constraints ("send to 15 per day"); per-group selection ("best person at each company", "one entry point per account"); recurring batch selection from a backlog; or selection plus personalised outreach in one request (nomination selects, then content generation writes).

## Step 1 — Individual, comparative, rank, or queue?

| Selection type | Pattern | When |
|---------------|---------|-------------|
| **Individual evaluation** | Path A — Persona Gate | Each entity judged independently against selection criteria; selection is about entity TYPE, not relative rank |
| **Comparative evaluation** | Path B — Best-Per-Group | The AI must see the full group to choose (5 qualified people at one company → which is the best entry point?). Path A can't compare across candidates |
| **Rank-based** | Score-and-Rank (Limit node) | Scores already exist; sort and take top N — no additional AI |
| **Queue-based** | Priority Queue (Limit + Scheduler) | A recurring run nominates the next batch from a backlog each time — nomination-over-time |

## Step 2 — Identify the nomination signal

| Signal | Source | Mechanism |
|-------------------|--------|---------------------|
| Persona fit (role type) | Title + headline → Classifier output | Path A: Filter `nominated_persona != "Others"` |
| Qualification score | Upstream `score` column | Limit: sort by score descending, top N |
| Manual priority | Human-assigned Priority column in the queue sheet | Limit: top N per Owner per run |
| Comparative judgment | All candidates per company | Path B: Group by company → Ask AI selects best entry point |
| Engagement signals | Activity scores, post frequency, reactions | Limit: sort by engagement metric, top N |

## The Four Patterns

### 1. Score-and-Rank (Limit node)

```
[Qualified entities with scores]
    ↓
Limit: limit_across_groups: true, grouping_keys: ["Owner"] or ["Company"],
       limit: N, column_to_sort: "Priority" or "score", sorting_order: descending
    ↓
[Top N nominees per group]
```

- `limit_across_groups: true` + `grouping_keys` makes the limit per-group, not global.
- Calibrate N to downstream capacity (rate limits, budget).
- `column_to_sort` must exist upstream — from qualification scoring or manual priority.

### 2. Persona Gate (Path A)

```
Classifier (gpt-4.1-mini): Content: Title: {{title}}\nHeadline: {{headline}}
    Keys: Finance Leaders | Revenue Operations | Others
    ↓
Filter: nominated_persona != "Others"
```

- **Nomination categories must be MORE selective than qualification categories.** Qualification separates fit from unfit; nomination separates "best target persona" from "everyone else". "Others" here captures qualified-but-not-the-right-type entities. Example keys: Finance Leaders (CFO, VP Finance, Controller — strategic budget holders), Revenue Operations (RevOps, GTM Operations — process and tooling decision makers), Others.
- Always include the "Others" catch-all — false positives are costly here because the downstream action (outreach, connection request) has real per-entity cost.
- Feed minimum fields relevant to the selection decision (`title` + `headline` for persona; engagement scores for engagement-based selection).
- Include `reason` in output — debugging AND a personalisation hook for the outreach angle.
- Layer deterministic filters: `nominated_persona != "Others" AND seniority in [Director, VP, C-Suite]`.
- Ask the user for their target persona definitions if not supplied.

### 3. Best-Per-Group (Path B)

```
[Qualified people per company]
    ↓
Create Column: "Person: {{full_name}} | Title: {{title}} | Seniority: {{seniority}} | LinkedIn: {{linkedin_url}}"
    ↓
Group by company keys (domain_name, Account Name) — unique aggregation
    ↓
Ask AI: "From this group, select the best entry point for [objective]. Return the selected person's details."
    ↓
[One nominee per company]
```

- Grouping key = the entity you want one nominee per (company for best-person-per-company; segment for best-company-per-segment).
- `unique` aggregation avoids duplicates biasing the choice.
- Output one row per group with the nominee's identifying columns preserved for any downstream merge back.

### 4. Priority Queue (nomination-over-time)

A Scheduler triggers the queue-based drip workflow (see gtm-automations skill); each run the Limit node nominates the top N per Owner sorted by Priority. The Priority column is the nomination signal — set manually by the sales team in the queue sheet or computed by a prior qualification step.

## Coupling with Content Generation

When the user asks for selection AND outreach ("find the right person at each company and write a connection message"), sequence two operations: nomination selects, then template-driven content generation (see content-generation skill) writes per nominee. The nominee's research data (events, scores, posts) feeds the content prompt; the persona label from nomination doubles as the cohort key for template selection — classifier categories must match the template sheet's cohort keys exactly. Use a high-quality model (gpt-5.2) and generate all pieces in one call.

## Downstream

Nominees flow to: content generation; GTM sends/CRM writes; queue insertion for drip processing; or a Google Sheet export for human approval before action.

## Build and Verify

Confirm Limit/Classifier settings against the live schema with `get_node_type` (never fabricate field names like `limit_across_groups` without checking), configure via `update_node_settings`, and test the gate with `run_node` — inspect `get_node_output` to confirm the persona distribution and that "Others" is actually catching non-targets before wiring downstream actions.

## Boundaries

Nomination ends with a specific, ordered list of entities selected for action. Assembling the initial list is **List Building**; gathering intelligence is **Research**; broad fit determination is **Qualification**; executing the action is **GTM Automations**.
