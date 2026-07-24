# workflow_studio: one-off data tool federation — findings & required changes

Handoff from the `nrev-mcp` side after staging testing surfaced three related
defects in the node-as-tool federation (`company_data__get_company_news`,
tenant 4, `version-1.0.0`). All three were validated against the `nrev-mcp`
source; this doc is scoped to what needs to change **in workflow_studio**
specifically (the embedded MCP server nrev-mcp federates at
`<workflow_host>/mcp`). The corresponding nrev-mcp-side changes (error
normalization, richer `list_data_tools` hints, doc fix) have already shipped
in this repo (`tools_data.py`, `shared/skills/one-off-research/SKILL.md`) and
are noted per issue below as client-side mitigation, not a substitute for the
fixes here.

Audience: whoever owns the node-as-tool federation / spend-gate code in
workflow_studio.

---

## Issue 1 — spend-gate estimate does not validate settings

**Severity:** High — a user can approve real spend against a call that is
guaranteed to fail.

**Repro (2/2):**
1. `POST /mcp` `tools/call company_data__get_company_news`,
   `settings={"domain": "stripe.com"}`, `confirm=false`
   → `{"status": "blocked", "estimate": {"credits": 2, ...}}` (clean).
2. Same tool, same `settings`, `confirm=true`
   → `INVALID_INPUT`, `credits_charged: 0`.

`trace_id: 6fecd32d-ff29-4e75-9166-70b1f45c3708`

**Finding:** the estimate path and the settings-validation path are not the
same gate. `confirm=false` quotes a credit cost without confirming the
payload is even structurally valid; `confirm=true` runs the real validator
and rejects the identical payload. Ruled out on the nrev-mcp side: the client
sends byte-identical `settings` on both calls (it does zero shape-checking of
its own today), so the discrepancy cannot originate there — it's a
sequencing/ownership gap between the two code paths in this service.

**Root cause (from the observable behavior, to confirm against the actual
service code):** either (a) the estimator and the validator are two
independent branches off `tools/call`, with the validator only invoked on the
`confirm=true` path, or (b) they're the same branch but estimation happens
before validation instead of being gated by it.

**Required fix:** make settings validation a **precondition** of estimation,
not a parallel or later step. Both `confirm=false` and `confirm=true` should
run through the identical validator before anything else happens:

```
validate(settings)
  → invalid: return INVALID_INPUT (no estimate, no charge)      # NEW
  → valid:
      confirm=false → estimate() → return blocked+estimate
      confirm=true  → estimate() → execute() → return result
```

A structurally invalid payload must never reach the estimator. The estimate
response and the execute response for the same `(tool, settings)` pair should
never be able to disagree on validity.

**Test to add:** for every tool-eligible node, a case that sends
structurally-invalid `settings` with `confirm=false` and asserts the response
is `INVALID_INPUT`, not a credit estimate.

**Client-side mitigation already shipped (nrev-mcp, not a fix for this):**
none for this issue specifically — flagged in the nrev-mcp source
(`tools_data.py` module docstring) as a known gap the client cannot close on
its own; a pre-flight schema check on the client would only catch
structurally-obvious cases and would give false confidence that the estimate
step validates. This issue must be fixed here.

---

## Issue 2 — unstructured validator error crosses the MCP boundary

**Severity:** Medium — breaks the error contract every agent using this
federation is told to rely on, and leaks an internal exception representation
to the end user.

**Repro:**
`settings={"company_details": [{"field_name": "domain", "field_value":
"stripe.com"}]}`, `confirm=true` →
```
MCP tool error: Input validation error: [{'field_name': 'domain', 'field_value': 'stripe.com'}] is not of type 'object'
```

**Finding:** this is the `str()` of a Python `jsonschema.ValidationError` (a
Python-dict repr embedded in prose), returned as the tool's error content
directly — not caught and translated into a structured error. This is
inconsistent with Issue 1's second call, which *did* produce a structured
`INVALID_INPUT` result. Two different failure surfaces exist today for what
is functionally the same class of problem (settings fail schema validation),
depending on which code path catches it.

**Root cause:** at least one call path into the settings validator (the
`tools/call` handler, most likely — since `list_tools`'s advertised
`inputSchema` doesn't fully describe the constraint that tripped here; see
Issue 3) does not wrap `jsonschema.validate(...)` (or equivalent) in a
try/except that maps `ValidationError` → the service's own structured error
type. It's likely the `confirm=true` execute path specifically, since the
`INVALID_INPUT` case in Issue 1 was also a `confirm=true` call but produced a
structured result — meaning the structured path exists in this service
already, just isn't applied uniformly at every point settings get validated.

**Required fix:**
1. Find every call site that runs settings through jsonschema (or the
   equivalent validator) for a tool-eligible node and ensure each one is
   wrapped to catch the validator's exception type and re-raise/return it as
   the service's structured error object — the same shape already used
   elsewhere (the `INVALID_INPUT` + `credits_charged` result seen in Issue 1).
2. Standardize that structured error shape explicitly (nrev-mcp needs this
   published, not just observed) as roughly:
   ```json
   {"error_class": "INVALID_INPUT" | "VENDOR_ERROR" | "CREDITS_EXHAUSTED",
    "message": "<human-readable, no internal repr>",
    "details": {"field": "...", "expected": "...", "received": "..."} }
   ```
   `details` matters — Issue 3's repro found `INVALID_INPUT` responses
   currently return `details: null`, giving the caller nothing to act on
   beyond "something is wrong." Populating `details` with the offending field
   path and expected type turns Issue 2 and Issue 3 into a single self-
   documenting error instead of two separate defects.
3. Audit for the same unwrapped-validator pattern on any other exception type
   that can escape a tool-eligible node's settings/execute path (not just
   jsonschema) — this was found via one node; treat it as a class of bug,
   not a single call site.

**Test to add:** structurally-invalid settings (missing required key, wrong
type, extra property under `additionalProperties: false`) should each produce
the *same* `error_class: INVALID_INPUT` structured shape, regardless of
which validation stage catches them.

**Client-side mitigation already shipped (nrev-mcp):** `run_data_tool` now
normalizes any upstream error into `{"status": "error", "error_class",
"message", "details"}`, including a best-effort classification of raw
jsonschema-style text as `INVALID_INPUT` when it matches known validator
vocabulary (falls back to `UNKNOWN` otherwise, deliberately — it does not
guess unrecognized errors into `VENDOR_ERROR`, since that would encourage an
agent to retry a bad-input error blindly). This is a backstop so agents get a
stable field today; it should become dead code once every path here emits the
structured shape natively — a heuristic string-matcher is not a substitute
for the real fix.

---

## Issue 3 — no documented/discoverable shape for group-envelope settings

**Severity:** Medium — forces trial-and-error against a credit-metered
endpoint; caused Issues 1 and 2 to be found from a wrong-shape guess rather
than a documented contract.

**Repro:**
- Shipped nrev-mcp doc suggested `settings={"domain": "stripe.com"}` for
  `company_data__get_company_news` → fails.
- Only working shape (verified: 100 rows, 2 credits,
  `trace_id: b586a859-f648-443f-91c3-084f733a1905`):
  `{"company_details": {"domain": "stripe.com"}}`.

**Finding:** this shape is a *third* settings contract, distinct from both
documented native-node shapes used inside workflows (flat single-value, and
the raw `[{"field_name": ..., "field_value": ...}]` group envelope — see the
`node-settings` skill for those two, verified against production workflow
building). The federation apparently flattens the raw node envelope into a
simplified nested-object contract for one-off callers
(`{"<group>": {"<key>": <value>}}` rather than
`{"<group>": [{"field_name": "<full-prefixed-key>", "field_value": <value>}]}`).
That's a reasonable design choice — hiding the ugly envelope from tool
callers — but:
1. It is not documented anywhere the nrev-mcp team could find.
2. It is not fully reflected in the `inputSchema` the federation advertises
   via `tools/list` — for `company_details`, `inputSchema` types the field as
   `object` but (per the repro that produced Issue 2) does not appear to
   publish its nested `properties`/`required` deeply enough for a caller to
   derive the correct inner shape without guessing — a caller mirroring the
   *raw* node envelope shape (reasonable, since that's what's documented for
   the underlying node) gets the confusing jsonschema leak from Issue 2
   rather than a clear "expected shape is X" response.
3. It is inconsistent with the node's own raw-envelope shape used when the
   same node runs inside a workflow, with nothing signaling that one-off
   callers should use a different, simpler contract for the same node.

**Root cause:** the node-to-tool flattening logic that builds each tool's
`inputSchema` from the underlying node's `settings_schema` does not emit a
fully self-describing schema for group/reference-envelope fields — it
correctly types the group as `object` but doesn't reliably surface (or
doesn't surface at all, for at least this node) that object's own nested
`properties`/`required`, and the flattening's target shape (nested plain
object vs. list-of-envelopes) isn't documented as its own contract anywhere.

**Required fix:**
1. **Make `tools/list`'s advertised `inputSchema` fully self-describing** for
   every tool-eligible node: any settings field that is a group/reference
   envelope must publish its complete nested `properties` + `required` in the
   schema returned by `tools/list`, not just the outer `type: object`. This
   is the actual fix — once the schema is complete, a caller (human or
   agent) can derive the correct shape from `list_tools()`/`inputSchema`
   alone, no guessing, no doc drift possible.
2. **Publish the flattening contract** the node-to-tool federation uses
   (nested plain object with bare inner keys, vs. the raw
   `{field_name, field_value}` envelope used inside workflows) as an explicit,
   versioned rule — ideally the same rule for every group-envelope field
   across every tool-eligible node, so it's learnable once rather than
   per-node.
3. **Sweep every other Company Data group-envelope node** for the same gap —
   `get_company_jobs` (Fetch Jobs' one-off form), `get_company_tech`,
   `get_company_customers`, `get_company_partners`, `get_company_vendors` all
   use the same `company_details`/reference-group pattern per the node
   catalog and are likely exposing the identical incomplete schema.

**Test to add:** for every tool-eligible node whose underlying
`settings_schema` contains a group/reference field, assert `tools/list`'s
`inputSchema` for that tool includes the group's nested `properties` and
`required`, not just its outer type.

**Client-side mitigation already shipped (nrev-mcp):**
- `list_data_tools`'s hint builder now recurses two levels into any
  `object`/array-of-object settings property and surfaces the nested
  `fields`/`item_fields`, so *if* `tools/list` ever does start publishing the
  nested schema (item 1 above), nrev-mcp will show it to the agent
  automatically — no further client change needed once that ships.
- The shipped `one-off-research` skill doc was corrected to the verified
  working shape and now explicitly warns that one-off tool settings can
  differ from the raw node-envelope shape documented for workflow building.

This mitigates the *symptom* (nothing to show today, since `company_details`'s
current `inputSchema` has no nested `properties` to recurse into) but does
not close the gap — until item 1 ships, `list_data_tools` for this node still
can't show more than `{"name": "company_details", "type": "object",
"required": true}`, because that's all the upstream schema currently
contains.

---

## Summary

| # | Issue | Fix owner | Status |
|---|---|---|---|
| 1 | Estimate quotes credits for settings that fail execute-time validation | **workflow_studio** — sequence validate-before-estimate | Open |
| 2 | Raw `jsonschema.ValidationError` string crosses the MCP boundary instead of a structured error | **workflow_studio** — wrap validator exceptions at every call site into the existing structured error shape; standardize + publish that shape (incl. non-null `details`) | Open |
| 3 | Group-envelope settings fields have no discoverable/documented shape | **workflow_studio** — make `tools/list`'s `inputSchema` fully self-describing for group fields; publish the flattening contract; sweep sibling nodes | Open |

nrev-mcp-side mitigations for all three (error normalization, recursive
settings hints, corrected skill docs) have already shipped and require no
further action from this team — they exist so agents get a stable contract
in the meantime, not as a reason to deprioritize the fixes above.
