---
name: nrev-data
description: Use when the user wants data pulled RIGHT NOW, once, without building a workflow — "just get me the comments on this post", "what's the latest news on these companies", "who reacted to this", "pull their recent posts", "check what they're hiring for". Covers the list_data_tools → run_data_tool estimate/confirm loop, spend discipline, save_to_table persistence, error recovery, and the search-query playbook. If the user wants it recurring or at scale, hand over to nrev-build instead.
---

# One-off data research

One-off data tools are platform workflow nodes exposed as directly callable
MCP tools — same vendors, same data, no workflow required. A tool name is the
node's type id with the dot replaced by a double underscore:
`linkedin_scraping__get_post_comments`, `company_data__get_company_news`
(`<node_family>__<action>`). The catalog GROWS as more nodes become
tool-eligible — at launch it covers LinkedIn activity (profile/company posts,
post comments, reactors, post search) and company signals (news, jobs, tech,
customers, partners, vendors); enrichment, people search, web search, and
scraping arrive in later tranches. **Never assume the catalog from memory —
always discover with `list_data_tools`.**

## The protocol

### 1. Clarify the objective
Pin down: the entity (which post? which companies — names or domains?), the
fields the user actually needs, expected volume, and whether this is truly
one-time. "Every week" / "whenever X happens" / hundreds of entities → this is
a workflow; load **nrev-build** and build one instead.

### 2. Check tenant context
`get_knowledge_base` / `search_knowledge` may already hold the ICP,
competitors, tracked accounts, or preferred providers that shape the pull
(e.g. "our competitors" resolves to concrete domains). If the knowledge base
is empty on the point you need, ask the user — don't guess.

### 3. Discover what's available
`list_data_tools()` — read the returned names, descriptions, and settings
schemas. Match the objective to tools. If nothing fits, say so plainly and
offer the alternatives: an existing nRev table that may already hold the data
(`list_tables` / `get_table_rows`), or a workflow build where the node catalog
is far larger (`find_node`).

### 4. Build the settings
`settings` mirrors the underlying node's schema as returned by
`list_data_tools` — use exactly those field names; never invent them, and
never assume a flat shape. Some fields are **group envelopes**: `type:
"object"` at the top level, with the real required key(s) one level down.
`list_data_tools` surfaces that nesting as a `fields` list on the field's hint
(`item_fields` if it's a list-of-objects group) — build the value as the
matching nested object, e.g. for `company_data__get_company_news`:
`{"company_details": {"domain": "stripe.com"}}`, not `{"domain": "stripe.com"}`.
This nested contract is the one-off tool's own simplified shape — it is
**not** the same as the raw `{field_name, field_value}` envelope documented in
**node-settings** for the same node used inside a workflow; don't copy that
shape here, and don't assume every one-off tool is flat just because some are.
Before writing settings for any provider-backed tool, consult
**data-provider-quirks** (LinkedIn URN vs URL, domain formats, pagination
pairs) and **provider-selection** if several tools could serve the job.

### 5. Estimate first — never confirm blind
```
run_data_tool(tool_name="company_data__get_company_news",
              settings={"company_details": {"domain": "stripe.com"}})
```
With `confirm` unset (or false) the call does NOT execute: it returns a credit
ESTIMATE and blocks. This first call is mandatory. **Never pass
`confirm=true` on the first call**, no matter how cheap the tool looks.

A clean estimate is not a full validation guarantee — the platform's settings
validator runs at execute time, not estimate time. If a `confirm=true` call
comes back INVALID_INPUT on settings that just estimated cleanly, that's a
known platform gap, not something you did wrong — re-check the tool's hint
(including nested `fields`/`item_fields`) against what you sent, fix, and
re-estimate; don't assume the estimate was a validity check.

### 6. Get explicit approval
Show the user: what will be fetched, from which provider, and the estimated
credits. One line is fine ("~6 credits to pull news for these 3 domains —
proceed?"). WAIT for an explicit yes. If the user asks a follow-up, answer it
and ask again. For batches over ~10 entities, pilot 3–5 first, show the hit
rate and per-entity cost, then ask before running the rest.

### 7. Execute
Re-call `run_data_tool` with the SAME tool_name and settings plus
`confirm=true`. Don't silently change settings between the estimate and the
confirmed call — if the settings change, re-estimate.

### 8. Present compactly
Summarize: row count, the columns that matter, 3–5 representative rows or a
synthesis keyed to the user's question. Never dump raw JSON. Flag empty or
partial results honestly (empty is often a real answer — "no news events in
the window").

### 9. Offer persistence
Results live only in the conversation. For anything worth keeping:
```
save_to_table(rows=[...], table_name="competitor_news",
              create_if_missing=True)
```
This writes to an nRev Table — permanent, queryable (`get_table_rows`,
`aggregate_table`), and usable as input to any future workflow. Use
snake_case column names (they become `{{template}}` references if a workflow
later reads the table). Prefer `table_id` when appending to a known existing
table; `table_name` + `create_if_missing=True` for a fresh one. Dedupe rows
before saving.

### 10. Offer the recurring upgrade
If the result is useful, ask: "want this running on a schedule / on a
trigger?" If yes, load **nrev-build** — the saved table becomes the
workflow's seed or destination, and every one-off tool has a corresponding
node, so the conversion is mechanical.

## Spend discipline

- The estimate/approve/confirm loop applies to EVERY `run_data_tool` call —
  including retries and pagination pages. Approval for one call is not
  approval for a loop of calls; state the total when you plan multiple.
- Cost multiplies per entity: a "quick check" across 50 domains is 50 calls.
  Dedupe and trim the entity list BEFORE estimating.
- Paginated tools (comments, reactors, posts): fetch page 1, check whether it
  already answers the question, and ask before pulling more pages.
- If an estimate looks disproportionate to the question, say so and offer a
  narrower pull.

## Error handling

`run_data_tool` never raises for a failed tool call — a failure is a normal
return with `status: "error"` and an `error_class`:

| error_class | Meaning | Action |
|---|---|---|
| `INVALID_INPUT` | Settings are malformed for this node (wrong field, bad URL/URN/domain format, wrong shape for a group field) | Re-check the tool's hint from `list_data_tools` (including nested `fields`/`item_fields`) against what you sent; fix, re-estimate, re-confirm |
| `VENDOR_ERROR` | Upstream provider failed or rate-limited | Not your settings' fault. Retry once after a pause; if it persists, tell the user and suggest trying later — don't hammer |
| `CREDITS_EXHAUSTED` | Tenant is out of credits | STOP all data calls. Tell the user to top up in the platform; do not retry |
| `UNKNOWN` | The failure didn't match a recognized shape | Don't assume it's your settings and don't loop retries — surface `message` (and `details`, when present) to the user verbatim and ask before trying again |

`details` (when present) names the specific field/expectation that failed —
show it alongside `message`, it's usually the fastest fix.

A tool that succeeds but returns zero rows is not an error — verify the input
(right domain? right URN?) against data-provider-quirks, then report the empty
result as a finding.

## Query-pattern playbook (for web-search tools)

When search-type tools appear in `list_data_tools`, these Google patterns
find GTM data fast. Rules: quote exact phrases (`"VP Sales"`), `OR` must be
UPPERCASE, exclude with `-recruiter`, always add a recency window for
time-sensitive content (`tbs=qdr:d`/`w`/`m`, custom
`cdr:1,cd_min:MM/DD/YYYY,cd_max:MM/DD/YYYY`).

| Target | Pattern |
|---|---|
| LinkedIn people | `site:linkedin.com/in "VP Sales" fintech -recruiter` |
| LinkedIn posts | `site:linkedin.com/posts (handle1 OR handle2) [topic]` — handles unquoted |
| LinkedIn jobs | `site:linkedin.com/jobs/view [role] [company]` (`/view`, not `/search`) |
| Job boards | `site:boards.greenhouse.io [company]`, `site:jobs.lever.co`, `site:jobs.ashbyhq.com` |
| Twitter/X | `site:twitter.com` and `site:x.com [person] [topic]` (both domains) |
| Reddit intent | `site:reddit.com/r/[subreddit] "alternative to [product]"` |
| G2 evaluation | `site:g2.com/compare [product1] vs [product2]` |
| Funding | `site:crunchbase.com/funding_round [company]`, or `"[company]" "raised" OR "Series"` + tbs |
| Local businesses | `site:yelp.com [type] [city]` + `site:instagram.com [type] [city]` — Google Maps is NOT indexed |

LinkedIn-specific extraction — post URLs encode what you need without another
search: `linkedin.com/posts/HANDLE_slug-activity-URN-noise` → the author
handle sits between `/posts/` and the first `_`; the numeric URN sits between
`activity-` and the next `-`, and feeds directly into the post-comments /
post-reactors tools. Google indexes LinkedIn with hours-to-days lag: `qdr:h2`
may return 0 results — warn the user and widen the window rather than
silently overriding their request. Google `site:linkedin.com/posts (handle)`
also returns posts that merely MENTION the handle — post-filter by extracting
each result's handle from its URL.

When a LinkedIn scraping tool covers the need directly (posts by person or
company, post search), prefer it over Google — fresher, structured, no
false positives. Use Google patterns to DISCOVER handles, post URLs, and job
listings; use direct tools to retrieve.
