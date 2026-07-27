---
name: provider-selection
description: Use when deciding WHICH data provider or tool should serve a data need — Apollo vs RocketReach vs PredictLeads vs LinkedIn scraping vs Parallel Web vs Google — before running a one-off data tool or picking a workflow node. Covers the job-to-provider decision matrix, each provider's strengths and blind spots, and auto-route rules. For HOW to configure the chosen provider, load data-provider-quirks; for gap-filling across providers, load waterfall-enrichment.
---

# Provider selection

Pick the provider BEFORE picking the tool or node. The same provider backs
both surfaces: one-off data tools (`run_data_tool`, discovered via
`list_data_tools`) and workflow nodes (discovered via `find_node` /
`describe_node`). Capability availability differs — the one-off catalog is a
growing subset of the node catalog — so after choosing the provider, verify
the surface: check `list_data_tools` for a matching tool; if absent, the
capability still exists as a workflow node.

## Decision matrix

| I need to... | Provider | Node family / typical tool |
|---|---|---|
| A person's recent LinkedIn posts | LinkedIn scraping | `linkedin_scraping__get_post_by_person` |
| A company's recent LinkedIn posts | LinkedIn scraping | `linkedin_scraping__get_posts_by_company` |
| Commenters on a LinkedIn post | LinkedIn scraping | `linkedin_scraping__get_post_comments` — each commenter is a warm signal |
| Reactors on a LinkedIn post | LinkedIn scraping | `linkedin_scraping__get_post_reactors` |
| Filter-search LinkedIn posts by keyword/author/time | LinkedIn scraping | `linkedin_scraping__search_linkedin_posts` |
| Full LinkedIn person/company profile from a URL | LinkedIn scraping | get_person_profile / get_company_profile |
| Company news & business events | PredictLeads (company_data) | `company_data__get_company_news` — categorized events, not raw text |
| Company job openings (hiring signals) | PredictLeads (company_data) | `company_data__fetch_jobs` — dedicated jobs API beats scraping |
| Company tech stack | PredictLeads (company_data) | `company_data__get_company_tech` — detects actual usage |
| Company customers / partners / vendors | PredictLeads (company_data) | get_company_customers / _partners / _vendors — B2B relationship graph |
| Look-alike companies | PredictLeads (company_data) | get_company_lookalikes — ML similarity |
| Search people by title + company + location | Apollo (people_data) | search_people — largest B2B database, best filters |
| Enrich a person for email / firmographic context | Apollo (people_data) | enrich_people — best email match rate |
| Find alumni of a company (past employees) | RocketReach | rocketreach search — `previous_employer` actually works |
| Find people by school/university | RocketReach | rocketreach search — only working `school` filter |
| Phone numbers | RocketReach | rocketreach enrich — best phone coverage (expensive) |
| Search companies by revenue/funding/growth | RocketReach | rocketreach company search — filters Apollo lacks |
| Enrich a company by domain | Apollo (company_data) | enrich_company — richest firmographics |
| Google/SERP search | Google (serp) | web search with site:/tbs operators — see one-off-research playbook |
| Scrape or extract content from known URLs | Parallel Web | markdown extraction; handles JS and PDFs |
| AI web research / structured extraction | Parallel Web | natural-language objectives with citations |
| Max-coverage email/phone waterfall | BetterContact | workflow-level managed waterfall — see waterfall-enrichment |
| Verify email deliverability | ZeroBounce | workflow-level validation before any send |

## Auto-route rules

1. Input is a LinkedIn `/in/` or `/company/` URL → LinkedIn scraping (direct,
   freshest). Don't Google-search what you already have a URL for.
2. Need post-level data (posts, comments, reactors, post search) → LinkedIn
   scraping is the only provider that exposes it.
3. School or past-employer filter → RocketReach. Apollo's equivalents are
   unreliable.
4. Company signal (news / jobs / tech / customers / partners / vendors /
   lookalikes) → PredictLeads via the company_data family. Requires a DOMAIN —
   resolve names to domains first (Apollo enrich or Google).
5. Phone numbers → RocketReach; treat as expensive and confirm the user
   actually needs phones before requesting them.
6. Scrape/extract/AI-research on the open web → Parallel Web. Broad SERP
   discovery → Google.
7. Everything else (people/company search and enrichment) → default Apollo.

## Provider profiles — strengths and blind spots

**Apollo** — largest B2B database; best title/seniority/department/location
filtering; rich company firmographics. Blind spots: school and past-employer
filters are unreliable; people SEARCH returns no contact info (enrichment is
a separate, credit-costing step); phones are weak; European data thinner
than US.

**RocketReach** — the alumni/school superpower (`previous_employer`, `school`
filters work); best phone coverage; ~99% match on LinkedIn-URL lookups;
company search filters (revenue, funding, growth) Apollo lacks. Blind spots:
smaller database; shallower company enrichment; phone requests cost ~6x
email-only; some lookups return async and need polling.

**PredictLeads** — structured, categorized company signals: jobs (refreshed
~36h, history to 2018), tech detections, news events, financing, the
customers/partners/vendors relationship graph, ML lookalikes. Blind spots:
company-only (no people, no emails); domain-keyed only; coverage strongest
for US/EU companies.

**LinkedIn scraping** — live from LinkedIn, no index lag; the whole post
family (posts, comments, reactors, search) exists nowhere else; profile and
company enrichment by URL with near-perfect accuracy. Blind spots: needs a
URL or URN as input (no name/email lookup — resolve via Apollo or Google
first); strict input validation; rate-limits under bursty loops.

**Parallel Web** — AI-native extraction and research: clean markdown from any
URL (JS, anti-bot, PDFs), structured output with citations, batch-scale.
Blind spots: extraction does NOT search (needs URLs in hand); text-only;
deep-research tiers get expensive — size the tier to the question.

**Google (SERP)** — the universal discovery layer: profiles, posts, jobs,
reviews, funding news, businesses that exist in no B2B database. Blind
spots: unstructured results that need post-filtering; LinkedIn content is
indexed with hours-to-days lag; query quality decides result quality (use the
one-off-research playbook patterns).

**BetterContact / Hunter / ZeroBounce / Instantly** — email waterfall,
domain email discovery, validation, and sending respectively. These operate
at the workflow/app layer, not as one-off data tools; recommend them by fit
and configure through workflow nodes or connected apps.

## Choosing under ambiguity

- Ask what the data is FOR: a signal to act on (PredictLeads / LinkedIn
  activity), a contact to reach (Apollo / RocketReach), or content to read
  (Parallel Web / Google). The purpose picks the provider faster than the
  entity type does.
- Non-standard entities (local businesses, D2C brands, creators) are NOT in
  Apollo/RocketReach — go Google discovery + Parallel Web extraction.
- When two providers plausibly serve the job, prefer the one whose input you
  already hold (domain → PredictLeads/Apollo; LinkedIn URL → LinkedIn
  scraping; nothing but a name → Apollo/Google to resolve identity first).
- Missing fields after the first provider is not a provider-choice failure —
  it's a gap-fill job: load **waterfall-enrichment**.
