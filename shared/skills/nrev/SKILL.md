---
name: nrev
description: Start here for anything nRev — checks sign-in and tenant, then routes to the right path (one-off data pull, workflow build, or fixing a broken run).
disable-model-invocation: true
---

# Start here

A human typed `/nrev` because they don't know which command they need. Your
job is three things in order: confirm they can actually do anything, pick the
path, hand off. **Do not do the work here** — this skill routes, the target
skill executes.

If `$ARGUMENTS` is non-empty, treat it as the objective and skip the
open-ended question in step 2 — infer the path from what they wrote and say
which one you picked.

## 1. Preflight

Both checks, always, before routing:

- `get_auth_status` — if unset or expired, call `auth_login`. It opens the
  browser to sign in once and auto-refreshes after. Don't surface
  environments, shell commands, or file paths; just have them finish in the
  browser.
- `get_active_tenant` — tell the user which tenant by name. This is
  server-side state, not in the token: a user in several tenants can switch in
  the web app mid-session, so the same conversation can start resolving
  somewhere else. Naming it once anchors the work and catches the "why is my
  data empty" case before it costs anything.

Add `include_credits=true` to `get_auth_status` if the objective looks like it
will spend — a zero balance is better found now than after a plan is built.

## 2. Fork

One question decides everything. Ask it plainly if the objective doesn't
already answer it:

> Do you need this **once**, or should it **keep running**?

| Answer | Path | Load |
|---|---|---|
| Once — "get me X right now" | One-off data pull, no workflow | **nrev-data** |
| Repeatedly, on a schedule or trigger, or multi-step | Build a workflow | **nrev-build** |
| "It's already broken" — a run errored, a node won't validate | Diagnose first | **nrev-fix** |

Getting this wrong is the expensive mistake, and it goes one direction: users
ask for a workflow when a single `run_data_tool` call would answer them, then
pay in build time and credits for a graph they run once. When the ask is a
single pull against known entities — a domain, a LinkedIn URL, a named
company — check `list_data_tools` before agreeing to build anything.

The reverse trap is quieter. "Every week", "whenever someone replies", or a
few hundred entities is a workflow even when phrased as a one-off; a data tool
run that the user re-triggers by hand is not automation.

**Cold search is neither.** "Find me founders in India" — people/company
search from criteria rather than from a seed — exists only in workflow search
nodes, not in the one-off catalog. Say so and let the user choose to build a
search workflow rather than silently starting one.

## 3. Hand off

Load the skill from the table and follow it. Tell the user which path you took
and why in one line — they typed a generic command and should learn what to
type directly next time.

Nothing else in this skill applies once you've routed. In particular, do not
configure nodes from memory: the platform's node catalog, settings field
names, and option values are not in your training data, and **node-settings**
is required reading before writing any settings dict.

## If they just want to know what this thing does

Answer from here; no routing needed. nRev is a GTM execution platform. The
workflows are dataflow graphs — nodes pass tables of rows along edges, running
in topological order. Typical shapes: build a list of target accounts or
people from search criteria, qualify them against an ICP, enrich with contact
data, research signals (hiring, funding, tech stack, posts), generate
personalised outreach, and write results to a table, Sheet, CRM, or Slack.
Runs can be triggered on a schedule or by an event.

Point them at `search_plays` for pre-wired templates before building anything
from scratch, and at `/nrev-data` if they want to feel it out cheaply first.

## Credits

Executions spend real tenant credits. Estimate with `estimate_run_cost`, keep
nodes in test mode while iterating, and get an explicit go-ahead before any
full-volume run — `run_workflow` with live nodes is refused without
`confirm=true` for exactly this reason.
