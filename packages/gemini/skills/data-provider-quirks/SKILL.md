---
name: data-provider-quirks
description: Per-provider gotchas that waste credits or return wrong data when missed — consult BEFORE writing settings for any run_data_tool call or provider-backed workflow node. Apollo's newline-domain string and two-step search-then-enrich, RocketReach's free-text employer traps and name-variant expansion, PredictLeads domain-keyed lookups, LinkedIn URN extraction and pagination-token pairing, Parallel Web extraction limits, Google operator rules, and email-verification status codes.
---

# Data provider quirks

Tactical reference, not strategy — for WHICH provider, load
provider-selection. These quirks apply identically whether the provider is
reached through a one-off data tool (`run_data_tool` settings) or a workflow
node (node settings): same vendors underneath.

**Index:** [Apollo](#apollo) · [RocketReach](#rocketreach) ·
[PredictLeads](#predictleads-company_data-signals) ·
[LinkedIn scraping](#linkedin-scraping) · [Parallel Web](#parallel-web) ·
[Google / SERP](#google--serp) · [Email verification](#email-verification-zerobounce--hunter--bettercontact)

## Apollo

- **Search returns NO contact info.** People search is a two-step process:
  search (cheap) returns identities with placeholder emails; a separate
  enrichment call unlocks real emails/phones. Never present search-result
  emails as deliverable.
- **`q_organization_domains` is a STRING, not an array** — multiple domains
  are newline-separated: `"apollo.io\ngoogle.com"`. The #1 Apollo bug.
- **Employee ranges use COMMAS, not dashes:** `["1,10", "11,50", "51,200"]`.
- **Search by domain, not company name.** Free-text `q_organization_name`
  is unreliable — resolve name → domain first, then filter on domains.
- **Pagination ceiling: 100/page × 500 pages = 50,000 records.** Partition
  bigger queries by location or seniority.
- **Credits can burn on failed enrichments** — search first to confirm the
  person exists; enrich with the best identifier you have (email ≈
  linkedin_url > name+domain > name+company > name alone).
- **Phones are async, expensive, and often wrong.** Prefer RocketReach.
- **Title expansion goes horizontal, not vertical.** Expand to sibling
  functions ("marketing" → growth, demand generation, brand); put seniority
  in the seniority filter, never in title keywords. Omit noisy generics
  ("operations", "business") that attract wrong matches.
- `person_locations` = where the person lives; `organization_locations` =
  company HQ. Don't conflate.

## RocketReach

- **`previous_employer` is FREE TEXT with fuzzy matching** — always pass
  multiple variants: `["mindtickle", "MindTickle", "Mind Tickle"]` (OR
  logic). Exact match: escaped quotes `["\"IBM\""]` excludes subsidiaries.
- **Never search current employer by name** — use `company_domain`
  (exact). Resolve name → domain first. (Exception: `previous_employer` has
  no domain alternative.)
- **School names need variant expansion, every time:** abbreviation, abbrev +
  city, full name + city, full name comma city — `["IIT Kharagpur",
  "Indian Institute of Technology Kharagpur", ...]`. One variant = missed
  results.
- **Boolean logic:** multiple values in one filter = OR; different filters =
  AND; `-` prefix excludes (`["Engineer", "-Senior"]`). Multiple
  departments/titles go in ONE call, not one call each.
- **Lookups can return async** (`status: "searching"`/`"progress"`) — poll
  the status rather than treating it as a miss; cached emails may return
  immediately while the lookup continues.
- **Lookup accuracy ranking:** LinkedIn URL ~99% > email ~87% > name +
  employer > name alone (may return the WRONG person — spot-check).
- Pagination is 1-indexed, hard cap ~10,000 results; phone-inclusive calls
  cost ~6x email-only — request phones only when the user needs them.

## PredictLeads (company_data signals)

- **Everything is keyed by DOMAIN, never company name** — and the exact ROOT
  domain: `stripe.com`, not `www.stripe.com` or `Stripe`. Wrong/missing
  domain is the cause of most "not found" and wrong-company results. Resolve
  names to domains (Apollo enrich or Google) before calling.
- **Empty `data[]` ≠ not found.** The company can exist with no events of
  that signal type in the window — that's a real finding. A hard "not found"
  means the domain isn't in their index (check spelling, try the root).
- **Jobs refresh ~every 36 hours** — a posting from this morning won't be
  there yet; history is deep (2018+). Don't promise real-time.
- **Customers / partners / vendors come from the B2B connections graph** —
  relationship-level data, high signal but incomplete by nature; absence of
  a relationship is not evidence of absence.
- News events arrive categorized (expansion, acquisition, leadership change,
  layoff, product launch...) with date + source URL — filter by category
  instead of re-classifying with AI.
- Coverage is strongest for US/EU companies; expect thinner results in
  emerging markets.

## LinkedIn scraping

- **URL type validation is strict.** Person ops need `/in/<slug>` URLs;
  company ops need `/company/<slug>`. A `/company/` URL in a person tool (or
  `/jobs/`, `/posts/`, non-LinkedIn hosts anywhere) is rejected —
  `INVALID_INPUT`, fix the settings, don't retry as-is.
- **Post operations take the bare numeric URN (15–20 digits), NOT a post
  URL.** Extract it from a post URL — the digit run between `activity-` and
  the next `-` in `linkedin.com/posts/HANDLE_slug-activity-URN-noise` — or
  from a posts-tool response. The author's handle sits between `/posts/`
  and the first `_` (→ `linkedin.com/in/<handle>`).
- **Pagination params travel in PAIRS.** Page-2+ requests must send BOTH the
  offset (`start` or `page`, per the tool's schema) AND the opaque
  `pagination_token` — both copied from the PREVIOUS response's `pagination`
  object. Sending one without the other is rejected; tokens are not
  interchangeable across tools or entities. Stop when `pagination_token`
  comes back null.
- **Rate limits bite bursty loops** — a 429 backs off ~60s with a single
  retry. Space out multi-entity pulls; don't fan out dozens of profile
  scrapes in a tight loop.
- Data is scraped live (no cache) — freshest possible, but volatile: counts
  and reactor lists can shift between pages of the same pull.

## Parallel Web

- **Extraction does NOT search.** It only processes URLs you already have —
  discover URLs first (Google / LinkedIn tools), then extract.
- Extraction runs ~10 URLs per call (larger sets are batched); give an
  `objective` so excerpts focus on what you actually want from the page.
- **Research/enrichment tasks are ASYNC** — results come by polling, not in
  the submit response. Inputs cap around 15,000 chars; split bigger jobs.
- Deep-research tiers scale cost 10–100x from the basic tier — match tier to
  question depth (simple fact ≠ competitive analysis), and prefer the
  `-fast` variants for interactive use (same quality, same price).
- Freshness floor: cached content can be up to 10 minutes old even when
  forcing fresh fetches. Text-only output — no images.

## Google / SERP

- Quote exact phrases (`"VP Sales"`); `OR` must be UPPERCASE; exclude with
  `-term`; always constrain recency with `tbs` for time-sensitive content.
- LinkedIn is indexed with hours-to-days LAG — tight windows (`qdr:h2`) can
  legitimately return zero. Other platforms (Reddit, Twitter, news) index
  near-real-time.
- `site:linkedin.com/jobs/view` for individual postings (not `/jobs/search`);
  Google Maps pages are NOT indexed — discover local businesses via
  `site:yelp.com` / `site:instagram.com` instead.
- Results are noisy by design: `site:linkedin.com/posts (handle)` matches
  mentions, not just authorship — post-filter by parsing each result URL.
- Full pattern playbook: see one-off-research.

## Email verification (ZeroBounce / Hunter / BetterContact)

- **ZeroBounce: read `status` AND `sub_status` together.** `valid` → send;
  `invalid` → drop; `catch-all` → risky (10–30% bounce), segment separately;
  `do_not_mail` + `disposable`/`role_based` → drop; `spamtrap` → NEVER send.
  `unknown` costs no credit — retry later.
- **Hunter: finder ≠ verifier.** The email finder pattern-GUESSES from
  name+domain with a confidence score — treat score < 90 as unverified and
  run the verifier before sending. A 202 response means "still checking",
  not an error — poll again.
- **BetterContact is async** — submit returns a request id; poll until
  terminated. Phone enrichment costs ~10x email; you only pay for
  found-and-verified data (misses are free, catch-alls still charge);
  batches cap at 100 records. `first_name` + `last_name` +
  (`company_domain` preferred over `company`) is the minimum input;
  adding `linkedin_url` maximizes match rate.
