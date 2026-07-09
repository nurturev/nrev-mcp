---
name: list-building
description: Use when the user wants to build a workflow that assembles a list of target entities — companies, people, LinkedIn posts, or jobs — from search criteria ("find me...", "build a list of...", "who are the...", or an ICP description without specific names or domains). Covers route selection, Apollo vs RocketReach search node choice, filter availability, and the fork-qualify-rejoin company qualification pattern.
---

# List Building

List building covers everything from "I have nothing" to "I have a qualified list of entities to work with." The output is a table of entities with enough identifiers (linkedin_url, domain, email, name) for downstream operations. It answers: **who or what am I targeting?**

Invoke list building when:
- The user has no entity list and needs one assembled ("find me", "search for", "build a list of", "get me a list")
- The user describes target-audience criteria without specific names or domains
- The user describes an ICP that implies search + qualification — if the ICP, persona, or competitor context is referenced but not supplied, ask the user for it
- The user provides domains or company names and wants people found within them — account-based list building (Search People with a dynamic `{{domain}}` template)

Decompose the prompt into individual criteria BEFORE selecting a route or nodes. When the request is ambiguous, present the planned route briefly and confirm with the user before building.

## Step 1 — Classify every criterion by entity type

| Person-level criteria | Company-level criteria |
|----------------------|----------------------|
| Title, role, function keywords | Industry, vertical, business model |
| Seniority (director, VP, C-suite) | Headcount / employee count |
| Location (city, country) | Revenue, funding stage, funding recency |
| Department | Tech stack, tools used |
| Years of experience | Pricing model, growth model |
| Education / alma mater | HQ location |

## Step 2 — Check filter availability per criterion

Verify against the live node schema with `get_node_type` — never fabricate filter names or settings. Reference matrix:

| Criterion | Apollo Search People | RocketReach Search People | Search Company | If unavailable → |
|-----------|---------------------|--------------------------|----------------|------------------|
| Title/role keywords | Yes (fuzzy via `include_similar_titles`) | Yes | — | — |
| Seniority | Yes | Yes | — | — |
| Person location | Yes | Yes | — | — |
| Company domains | Yes | Yes | — | — |
| Employee count | Yes | Yes | Yes | — |
| Revenue range | Yes (min/max) | Yes | — | — |
| Years of experience | No | Yes | — | Use RocketReach |
| Department | No | Yes | — | Use RocketReach |
| Company funding stage | No | Yes | Recency only | RocketReach or Enrich + AI |
| Funding round type ("raised Series B") | No | — | Recency only | Enrich Company → Ask AI classification |
| Industry keywords | — | — | Yes | — |
| Company location | Yes (org location) | Yes | Yes | — |
| Business model | No | No | No | **AI + web research** |
| Pricing model | No | No | No | **AI + web research** |
| Tech stack / tools used | No | No | No | **Get Company Technology → Filter, or AI + web** |
| Growth model (PLG, sales-led) | No | No | No | **AI + web research** |
| Composite ICP fit | No | No | No | **AI + web research with rubric** |
| Post content / topic | No | No | No | **Google Search `site:linkedin.com/posts`, or fetch posts → AI** |
| Persona beyond title ("strategic budget holders") | No | No | No | **Classifier/Ask AI on title + headline (Path A)** |

Native filter detail (confirm with `get_node_type` before configuring):
- **Search People (Apollo):** title keywords with fuzzy matching, seniority (c_suite, founder, owner, head, director, manager, senior, entry), person location, organization location, company domains, employee count buckets, revenue min/max.
- **Search People (RocketReach):** all of Apollo's plus department, company industry, company funding range, company revenue, education/alumni, years of experience, recently changed jobs.
- **Search Company (Apollo):** location (city/state/country, comma-separated, mixable), employee count buckets ('1-10', '11-20', '51-100', '101-200', '201-500', '501-1000', '1001-2000', '2001-5000', '5001-10000', '10000+'), industry keywords (use comprehensive synonyms, e.g. "Pharmaceutical, Pharma, Drug Development, Drug Manufacturing"), funding recency (e.g. after: last_3_months).

## Step 3 — Identify the unavailable filter (complexity driver)

If ANY criterion is unavailable as a native filter, it is the complexity driver. State it explicitly in the plan: "The criterion '[X]' is not available as a platform search filter on any node. It requires [AI + web research / enrichment + filter / Google Search], which forces the workflow into [Route 2a / Route 1 with AI / Route 3]." State the expected volume at each stage (e.g. "10,000 people → ~2,500 unique companies → ~1,500 after pre-filter → AI on 1,500").

## Route Selection

| User's starting point / prompt characteristics | Route | Flow |
|----------------------|-------|------|
| Company-level criteria drive the search and are expressible as Search Company filters, then find people within | **Route 1 — Company First** | Search Companies → qualify → Search People within qualified domains |
| Company-level criteria drive the search but include non-searchable criteria | **Route 1 with AI qualification** | Search Companies broadly → Enrich → AI + web qualify → Filter → Search People within survivors |
| Person-level criteria only, all resolvable by search filters | **Route 2 — People First** | Search People → optional Path A persona classification |
| Person-level criteria drive the search, but one or more company-level criteria are NOT search filters | **Route 2a — People First with Company Qualification** | Fork-qualify-rejoin (below) |
| Criteria too niche/unconventional for platform search ("music tour managers", "Instagram fitness influencers") | **Route 3 — Google Search** | Google Search with `site:` operators → parse → enrich → classify |
| Posts from specific companies (have or can obtain company LinkedIn ID) | **Route 4 — LinkedIn Post Search** | Get Company Profile → Search Posts by company_id |

For topic-based post discovery ("posts about AI in sales"), use Google Search with `site:linkedin.com/posts [topic]`, NOT Route 4.

### Route 1 — Company First

- Cast a broad net on company search; platform company filters are limited. Encode available filters, use enrichment + web research + AI classification for everything else.
- **Always qualify companies before searching people within them.** Company enrichment + AI qualification is cheaper than spending people-search credits on unqualified companies: Search Companies → Enrich / Ask AI (web research) → Filter → Search People (within qualified domains) → optional Path A on people.
- Search People within qualified companies is a 1-to-many expansion. Apply Path A (classify personas, filter) or deterministic filters (seniority, department) depending on nuance.

### Route 2 — People First

Search node selection:

| User criteria includes | Node |
|---|---|
| Fuzzy title matching across naming conventions | **Apollo** (`include_similar_titles: true` — "Revenue Operations" also matches "Rev Ops Manager", "Director of RevOps") |
| Past employment history needed in output | **Apollo** |
| Years of experience / company funding stage / granular revenue / department | **RocketReach** |
| Simple title + location + seniority | **Either — Apollo is default** |

**Enrich broadly at search time.** The search node is often the only place to request enrichment fields. Include everything downstream needs: grouping/merge keys (`org_primary_domain`, `org_linkedin_url`, `org_founded_year`), person identifiers (`linkedin_url`, `email`), context fields (`title`, `headline`, `seniority`, `city`). There is no second pass.

### Route 2a — Fork-Qualify-Rejoin

The most architecturally complex pattern. Used when person-level criteria drive the search BUT company-level criteria require AI judgment (e.g. "B2B SaaS companies").

```
Search People (broad criteria, high limit e.g. 10,000)
      ↓
   ┌──────────────────┬──────────────────────────┐
   ↓                                             ↓
[Raw people results]              [Group Data by company keys — dedupe]
   ↓                                             ↓
   │                              [Deterministic Filter — valid domain,
   │                               founded year, exclude VC, etc.]
   │                                             ↓
   │                              [Limit — cap companies for AI cost]
   │                                             ↓
   │                              [Ask AI with web research:
   │                               "Is this a B2B SaaS company?"]
   │                                             ↓
   │                              [Filter — keep qualified companies]
   ↓                                             ↓
   └──────────────────┬──────────────────────────┘
                      ↓
              [Merge — INNER JOIN on company keys]
                      ↓
              [People from qualified companies only]
```

1. **Phase 1 — broad search.** All available person-level filters; don't apply the unavailable company criteria yet.
2. **Phase 2 — company qualification branch.** Group Data on company-identifying columns (`org_primary_domain`, `org_linkedin_url`, `org_founded_year`) collapses thousands of people rows to unique companies (count aggregation shows people per company). Apply cheap deterministic pre-filters (domain contains ".", founded year, exclude VC industry tags), then a Limit cap, then Ask AI with web research returning structured output (qualification + reason).
3. **Phase 3 — rejoin.** INNER join qualified companies with the untouched raw people fork. Join keys must match the grouping keys exactly. Inner — not outer — because the goal is to discard people at non-qualifying companies.
4. **Cost architecture:** 10,000 people → ~2,000-3,000 unique companies → pre-filter to ~1,500 → AI on 1,500 instead of 10,000 (~85% cost reduction).
5. Optionally apply Path A persona classification on people after the merge.

### Route 3 — Google Search (the escape hatch)

| Use case | Query pattern |
|---|---|
| People on LinkedIn | `site:linkedin.com/in [role/criteria] [location]` |
| LinkedIn posts by topic | `site:linkedin.com/posts [topic] after:[date]` |
| Company pages | `site:linkedin.com/company [industry/criteria]` |
| Twitter/X profiles | `site:twitter.com [role/criteria]` |

Use quoted phrases for exact matching (`site:linkedin.com/in "tour manager"` avoids "travel manager" contamination). Add `after:YYYY-MM-DD` for recency. After Google Search, always parse profile URLs and enrich to get structured data before downstream processing.

### Route 4 — LinkedIn Post Search

Only for company-specific post discovery with a resolvable company LinkedIn ID: Get Company Profile first to resolve company_id, then Search Posts with it.

## Post-Search Refinement Patterns

**Path A — Individual Classify + Filter** (dominant post-search pattern): classify each row individually (people by persona, companies by ICP fit, Google results by relevance), then Filter.
- Always include a catch-all classification key ("Others") — without it the classifier forces false positives.
- Include `reason` in classification output — near-free, invaluable for debugging.
- Feed the classifier minimum fields: `title` + `headline` for people; full `description` for jobs.
- Layer deterministic filters on top of the AI label (seniority, date, engagement count).
- If N is large (500+), move deterministic filters BEFORE the classifier; if moderate (50-100), filter after for simplicity.

**Path B — Group Synthesise**: for merging overlapping lists from multiple sources, deduplicating entities (Apollo + Google Search), or producing company-level summaries from people-first searches. Group by entity key columns with `unique` aggregation; the grouping key sets the level of analysis.

## Key Practices

1. **Broad search, qualify later.** Don't over-constrain the search node; precision happens downstream.
2. **Choose the right start point** (company-level complex criteria → Route 1; person-level → Route 2; niche → Route 3).
3. **Enrich broadly at search time** — no second pass.
4. **Google Search is the escape hatch** when platform search can't express the criteria.
5. **Test the search node first.** Build it via `edit_workflow` (add_node), run it alone with `run_node` on a low limit, inspect `get_node_output`, and only then build downstream segments. Check `search_plays` for an existing play before building from scratch.

## Variants

- **Scheduled list building:** Scheduler trigger on a cadence; compute time thresholds from the scheduler timestamp in a Magic Node. Useful for ongoing TAM expansion (e.g. weekly Google Search for new posts with date thresholds).
- **Multi-source:** combine Apollo + Google Search results via merge; dedupe on linkedin_url or email (Path B).
- **List building + immediate research:** chain directly into parallel multi-signal research per qualified entity.
- **Incremental/delta:** maintain a "seen" list (Google Sheet or nRev table); LEFT JOIN + Filter `is_seen is_empty` anti-join to skip already-processed entities.

## Failure Modes

- **Too many irrelevant results** — tighten keywords, add seniority/location constraints at search time, strengthen the AI classifier downstream.
- **Too few results** — broaden filters, switch to Google Search, or go Company First.
- **Wrong semantic matches** ("travel managers" for "tour managers") — Google Search with quoted exact phrases, or AI classifier to remove false positives.
- **Credits spent before qualifying** — always qualify companies before searching people within them.
- **Enrichment fields missing downstream** — request all downstream-needed fields at the search node.

## Boundaries

List building ends with a table of entities with identifiers. Researching them in depth is **Research**; scoring/ranking is **Qualification**; picking specific ones is **Nomination**; acting on them is **GTM Automations**.
