---
name: waterfall-enrichment
description: Use when one provider's enrichment left holes to fill — missing emails, phones, domains, or firmographics across a list — and the fix is trying a second provider on ONLY the gap rows ("fill in the missing emails", "we only got 60% coverage", "try another source for the rest"). Covers the gap-fill sequence for one-off data tools and the filter-branch-merge pattern for workflows, coalescing rules, and when to use BetterContact's managed waterfall instead.
user-invocable: false
---

# Waterfall enrichment (gap-filling)

A waterfall is NOT "call every provider on every row." It is: enrich with the
best single provider for the data type, then run a SECOND provider only on
the rows where the field came back empty. Done right, provider B touches a
fraction of the list; done wrong, you pay 2x for data you already had.

## The rules

1. **One best provider per data type first** (see provider-selection):

   | Data type | Primary | Gap-filler |
   |---|---|---|
   | Work email | Apollo | RocketReach |
   | Phone number | RocketReach | — (accept the gap; phones elsewhere are poor AND expensive) |
   | Person firmographics (title, seniority, company) | Apollo | LinkedIn profile scrape (needs `linkedin_url`) |
   | Company firmographics | Apollo | RocketReach company lookup |
   | Company domain (from a name) | Apollo enrich | Google search |
   | LinkedIn URL | Apollo | Google `site:linkedin.com/in "name" "company"` |

2. **Gap rows only.** Filter to rows where the target field is empty before
   invoking provider B. Never re-enrich a row that already has the field.
3. **Coalesce, never overwrite.** Provider B's values fill blanks; they do
   not replace provider A's non-empty values. Track provenance in a
   `source` column when the user cares which provider supplied what.
4. **Cap at two providers per field.** A third pass rarely lifts coverage
   more than a few percent; report the residual gap instead.
5. **Order by hit rate ÷ cost.** The cheap high-coverage provider goes
   first so the expensive one sees the fewest rows.
6. **Verify before sending.** Waterfalled emails mix providers and confidence
   levels — validate deliverability (ZeroBounce) before any campaign use.

## One-off version (data tools)

Standard estimate → approve → confirm loop from **nrev-data** applies
at every step:

1. Run the primary provider's tool via `run_data_tool` on the whole list.
2. Persist: `save_to_table(rows, table_name=..., create_if_missing=True)`.
3. Find the gaps: `get_table_rows` with a filter on the empty field (or
   inspect the results directly for small lists). Report coverage to the
   user: "Apollo found emails for 34/50 — fill the remaining 16 with
   RocketReach for ~N credits?"
4. On approval, run the gap-filler tool on ONLY the gap rows.
5. Merge back with `save_to_table` into the same table (match on a stable
   key — linkedin_url or email or domain), filling blanks only.

The coverage checkpoint between steps 3 and 4 is the whole point: the user
decides whether the residual gap is worth the second spend.

## Workflow version (recurring)

Express the same logic as a graph (load **nrev-build** and
**node-settings** to assemble it):

```
Enrich (provider A)
  → split on "target field is empty"   (deterministic filter — cheaper than AI)
      ├─ has value ────────────────┐
      └─ empty → Enrich (provider B) ┴→ Magic Node: coalesce A/B, dedupe on key
                                       → write to nRev table / destination
```

- The split MUST be a cheap deterministic filter on the field, not an AI
  qualification node.
- The rejoin is a Magic Node (fan-in `df1`/`df2`), coalescing per field:
  `result["email"] = df1["email"].fillna(df2["email"])`-style logic, then
  dedupe on the stable key.
- Cost-aware ordering still governs: dedupe and disqualify BEFORE the first
  enrichment so neither provider sees junk rows.
- Test with a handful of rows in test mode and inspect both branches'
  `get_node_output` before a full run — silent row-level errors on the
  enrich nodes look like "gaps" and trigger pointless provider-B spend.

## When to skip the manual waterfall

For email/phone coverage at volume, a managed waterfall beats hand-rolling:
**BetterContact** cascades 20+ sources internally, only charges for
found-and-verified data, and returns one merged record. Prefer it (as a
workflow node / connected integration) when: the list is large (100+), the
target is contact info specifically, and the user cares about coverage more
than provider provenance. Hand-roll the two-provider gap-fill when: the field
isn't contact info (firmographics, domains, LinkedIn URLs), the list is
small, or the user wants explicit control over which provider is asked first.

Do NOT hand-roll a 20-provider cascade with data tools — two passes is the
ceiling; beyond that you are rebuilding BetterContact at retail prices.

## Reporting

Always close with the coverage arithmetic: rows in, coverage after provider
A, gap rows sent to provider B, final coverage, residual gap, credits spent
per stage. This is what makes the next waterfall decision (and the
convert-to-workflow decision) informed rather than hopeful.
