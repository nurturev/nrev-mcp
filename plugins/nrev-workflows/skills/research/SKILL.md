---
name: research
description: Use when the user already has (or the workflow will produce) a list of known entities and wants multi-signal intelligence gathered about them — "research these companies", "check their hiring/tech/posts/news", or on-demand single-entity research triggered by a Slack message or webhook. Covers parallel swimlane architecture, the standardised event schema, Path B collapse, persona gating, and event convergence.
---

# Research

Research sits between "I have a list of entities" and "I understand them well enough to score, qualify, or act." It answers: **what do I know about this entity?**

Invoke research when:
- The user has (or the workflow will produce) entities and wants to learn more ("research these companies", "what signals do we have", "investigate their background")
- Intelligence is needed across multiple data dimensions per entity — each dimension becomes a swimlane
- Signals must be synthesised into structured findings (events, scores, summaries) before action
- Content generation is requested but entity data is too thin for personalisation — research must run first
- Scoring/ranking needs multi-signal context

## Step 1 — Expand vague language into specific dimensions

| User language | Dimensions | Swimlane type |
|--------------|-----------|----------------------------|
| "Research the company" (general) | Hiring + tech stack + company posts + web research (M&A, news, firmographics) | Mixed |
| "Check their hiring activity" | Hiring pipeline (Fetch Jobs) | Platform node |
| "What tech do they use?" | Tech stack (Get Company Technology) | Platform node |
| "What are they posting about?" | Company posts (Get Posts by Company) or person posts (Get Post by Person) | Platform node |
| "Any news or acquisitions?" | M&A, news, press | Web research (Ask AI) |
| "Validate their firmographics" | Revenue, employee count, ownership | Web research |
| "Competitive landscape?" | Competitors, positioning | Web research |
| "Research them" (person-level) | Employment history (Enrich Person) + person posts | Platform nodes |
| "What signals do we have?" | All dimensions — full parallel architecture | All types |

## Step 2 — Classify each swimlane

| Swimlane type | When | Internal pattern | Model |
|---------------|-------------|-----------------|-------|
| **Platform node** | Data available via a native node (Fetch Jobs, Get Company Technology, Get Posts by Company/Person) | Fetch → optional recency filter → Create Column (metadata digest) → Group Data by entity key → Ask AI extracts standardised events | core-fast |
| **Web research** | No platform node — M&A, parent company, regulatory, competitive, firmographic validation | Ask AI with web research, single pass, no expansion/grouping | core-fast |

**Always prefer platform nodes when available** — structured, reliable data. Web research covers everything they can't. Find candidate nodes with `search_nodes` and confirm constraint fields (limit, date range, category) with `get_node_type`; never fabricate settings. Note: core-fast is a Parallel Web model with web research built in; the `web_search_enabled` toggle applies only to OpenAI models (see node-settings skill).

Observed platform swimlanes:

| Dimension | Platform node | Digest fields | Grouping key | Model |
|-----------|--------------|-------------------|-------------|----------|
| Hiring Pipeline | Fetch Jobs (domain, last_6_months) | `title`, `posting_url`, `description` | `domain_name` | core-fast |
| Tech Stack | Get Company Technology (domain, last_year) | `tech_title`, `tech_url`, `first_seen_at` | `domain_name` | core-fast |
| Company Posts | Get Posts by Company (linkedin_url, limit 50) | `post_url`, `text`, `posted` | `domain_name` | core-fast |
| Person Posts | Get Post by Person (linkedin_url_1, limit 50) | `post_url`, `text`, `posted`, `linkedin_url_1` | `domain_name` + `linkedin_url_1` + `name` | core-fast |

## Step 3 — Collapse every 1-to-many expansion (Path B)

Each platform swimlane fans out (one company → many jobs/posts) and MUST collapse back to one row per entity before merging. **If any swimlane skips the collapse, the merge produces a cartesian product — a structural failure.**

1. **Constrain at the fetch node, not after** — volume and recency limits at source; never fetch unbounded.
2. **Create Column builds the metadata digest** — one formatted text column per row: primary signal field, reference URL, temporal field, supporting context. Exclude everything else to cut token cost.
3. **Group Data with `unique` aggregation** — N rows → 1 per entity. The aggregate column name becomes the AI input variable (`Unique Jobs`, `Unique Technologies`, `Unique Posts`).
4. **Grouping key = the parent entity columns from before the expansion.**
5. **Ask AI with mini-class models** (core-fast, gpt-5-mini) — extraction, not reasoning.
6. **Deterministic recency pre-filter before grouping** (e.g. `posted date_is_after last_6_months`) to cut tokens.

## Step 4 — Persona gating inside swimlanes (Path A)

When fetching person posts for a broad people list, gate before the fetch — cost control via classification:

```
Search People (domain_name, senior titles, keywords from <<wf_var>>)
    → Ask AI "Persona Classification" (gpt-4.1-mini, on title + headline) → nominated_persona
    → Filter (nominated_persona != "Others")
    → Get Post by Person (limit 50)
    → Create Column digest → Filter (posted last 6 months) → Group by person → Ask AI extract events
```

Use a lightweight model; feed only `title` + `headline`; always include an "Others" catch-all; define persona keys with descriptions/examples matching the user's ICP (ask the user for persona definitions if not supplied); reference them via workflow variable for external configurability.

## The Standardised Event Schema

All swimlanes output the same structure regardless of source:

```json
{"events": [{
  "event_summary": "25 word summary",
  "event_url": "https://...",
  "event_type": "category",
  "event_date": "yyyy-mm-dd",
  "event_category": "Hiring Pipeline | Tech Stack Detection | Company Posts | Person Posts | Base Research"
}]}
```

- The shared schema is what makes merge, dedup, and scoring possible — events are interchangeable across swimlanes.
- `event_category` is hardcoded per swimlane to mark provenance; scoring can weight sources differently.
- Person post events add poster attribution (`poster_url`, `name`).
- **The schema is a contract** — downstream merges, scorers, and writers key on these names. Treat as immutable per workflow.

## Event Convergence Pipeline

```
[Append Merge all swimlane outputs]
    ↓
[Delete Column — drop intermediates: Unique Jobs, Unique Posts, Unique Technologies]
    ↓
[Split Data — column_to_split: "events" (explode the JSON array into rows)]
    ↓
[Create Column — extract nested fields: events.event_date, events.event_type, events.event_url, events.event_summary]
    ↓
[Group by entity key — aggregate unique events → "Unique Events"]
```

Delete intermediates before Split — they were only needed inside their swimlanes and inflate scoring tokens. Split + Create Column turn the JSON array into top-level columns accessible as `{{Event Date}}`. Append merges can be a binary tree or flat chain — same result.

## Merge Practices (batch research)

- **Outer joins** — if one swimlane fails for an entity (timeout, no data), the entity survives with nulls rather than disappearing.
- **Preserve merge keys unchanged through every swimlane** (`domain_name`, `linkedin_url`, `Account Name`) — renamed/dropped keys cause silent merge failures (duplicate rows instead of consolidation).
- **Descriptive, unique column names per swimlane** ("M&A activity", "Tech. & Tool Stack" — not generic "summary") to avoid collisions.
- **Clean up after merging** with Delete Column so downstream nodes get a curated row.

## On-Demand Research (listener-triggered single entity)

| Mode | When | Difference |
|------|------|------------|
| **Batch** | "Research these 500 companies" | Parallel swimlanes per entity, sequential pairwise merges |
| **On-demand** | "Research this company" via Slack message or webhook | Same swimlanes, plus: entity extraction from free text, pre-research enrichment, payload preservation for response delivery |

- **Entity extraction:** first AI step parses free text into identifiers with a lightweight model (gpt-5-mini). Companies: `{company_name, domain_name}` with fallbacks ("use domain for company name if absent"). People: `{person_name, linkedin_url}` with identifier priority ("prefer LinkedIn URL").
- **Pre-research enrichment:** Enrich Company first to resolve identifiers the swimlanes need (e.g. `linkedin_url`). Guardrail: swimlanes depending on enriched fields Filter at entry (`linkedin_url is_not_empty`).
- **Payload preservation:** the trigger payload (channel ID, thread timestamp) must survive the pipeline — fork the listener output to a Merge node early, join back at the end on the entity key.
- **Scoring with external framework:** inject via workflow variable; include score ranges and the output schema (`overall_score`, `section_wise_scoring`, `rationale_for_scoring`, `slack_summary`); add fallback messaging for low-data cases so the bot never returns empty.
- **Response delivery (Slack):** thread replies with `thread_ts = {{payload.ts}}`, `mrkdwn = true`, custom bot identity (`username` + `icon_emoji`), `unfurl_links = false`.

## Multi-Tier Model Strategy

| Stage | Model | Rationale |
|-------|-------|-----------|
| Entity extraction from message | gpt-5-mini | Simple parsing |
| Persona classification | gpt-4.1-mini | Short-input classification |
| Event extraction (all swimlanes) | core-fast | Web research + moderate reasoning on structured input |
| Final scoring & synthesis | gpt-5.2 | Multi-signal reasoning, nuanced scoring |
| Talking points / outreach | gpt-5.2 | Creative synthesis |

Use the cheapest model that reliably handles each task; reserve premium models for final synthesis where quality is user-facing.

## Workflow Variables in Research

Store per-deployment configuration in workflow variables (`<<wf_var.uuid>>`, managed via `manage_variables`) instead of hardcoding in prompts: scoring frameworks (categories + weights), persona definitions ("CFO, VP Finance, Controller → Finance Leader"), investor/reference lists, people-search keywords. Non-technical users can then reconfigure without editing structure. Ask the user for their ICP, persona, or competitor context when a variable's content is needed and not provided.

## Dimension Reference

| Dimension | Source | Type | Output |
|-----------|--------|------|--------|
| Hiring Pipeline | Fetch Jobs | Platform → Path B | Hiring events |
| Tech Stack | Get Company Technology | Platform → Path B | Technology adoption events |
| Company Posts | Get Posts by Company | Platform → Path B | Company activity events |
| Person Posts | Search People → Classify → Get Post by Person | Platform + Path A gate → Path B | Person activity events |
| News / M&A / Parent company / Firmographic validation / Competitive landscape | Ask AI with web research | Web research (direct) | Verified business events |
| Employment History | Enrich Person | Platform | Role changes, tenure |

## Build and Verify

Build one swimlane at a time with `edit_workflow` (add_node/add_edge); configure via `update_node_settings`; test each swimlane's fetch node with `run_node` on one entity and inspect `get_node_output` (use `download_node_output` for large post/job dumps) to confirm the digest and grouping before adding the next swimlane. Run the full graph with `run_workflow` and check `get_execution` for per-node status.

## Boundaries

Research ends when entities are enriched with structured findings. Assembling the initial list is **List Building**; standalone fit scoring against a rubric is **Qualification** (research may embed scoring only as part of on-demand response delivery); selecting entities for action is **Nomination**; executing outreach or CRM writes is **GTM Automations**.
