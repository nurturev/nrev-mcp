# nRev Workflows — agent context

The `nrev-workflows` MCP server exposes tools to build and operate GTM workflows
on the nRev platform, plus the nRev tables service (a lightweight database the
workflows read and write). This file is the **always-on** context; the deep
how-to lives in the bundled **skills**, which every supported agent loads on
demand (progressive disclosure).

> Canonical source. This file and `skills/` are maintained here in `shared/` and
> fanned out to each agent package by `scripts/sync-agents.sh`. Never edit the
> copies under `plugins/` or `packages/` — edit here and re-sync.

## First run — sign in once

If any tool returns 401, or `get_auth_status` reports unset/expired, call
`auth_login`. It opens a browser for Google sign-in and completes on its own;
the session is saved to `~/.nrev-workflows/credentials` and refreshed
automatically, so the user signs in only once. Don't ask which environment to
use or surface internal file paths — those are deployment details.

## Confirm the tenant

Call `get_active_tenant` and tell the user which tenant the work is anchored to
(by name). A user may belong to several tenants; the active one is server-side
state, not carried in the token, so it can change mid-session if switched in the
web app. This MCP never switches tenants itself. If a tool reports a tenant
change, **halt** and confirm with the user before continuing.

## Pick the path — one-off data vs building a workflow

Not every request needs a workflow. Decide before you build:

- **One-off data pull** — the default for exploratory / "get me this now" asks.
  `list_data_tools` lists the direct data tools the platform exposes;
  `run_data_tool` fetches the data with **no workflow**. First call
  (`confirm=false`) returns a credit estimate — show it, get the go-ahead, then
  re-call with `confirm=true`. Persist with `save_to_table`, then *offer* to
  automate it as a workflow — don't assume they want one.
- **Build a workflow** only when the user wants it to run **repeatedly**, fire
  on a **trigger**, or chain multiple steps — or when no data tool covers the
  need and the user agrees to the heavier build. Building a workflow to answer a
  one-off question wastes time and credits.

The data tools are **seed-based** — they enrich/scrape a known domain, LinkedIn
URL, or company. `list_data_tools` is authoritative, so check it. If the ask is
cold **people/company search** ("find me founders in India") and nothing there
matches, that capability lives only in workflow **search nodes** today: tell the
user a search workflow is the only path and let them choose — don't default to
building one.

## Spend safety

Executions consume tenant credits. Keep nodes in **test mode** while iterating.
A live `run_workflow` (any node not in test mode) is refused without
`confirm=true`; use `estimate_run_cost` and get the user's go-ahead before
spending real credits.

## Skills (loaded on demand)

- **nrev** — user-typed front door only (`/nrev`); preflight + path fork.
  Never load this yourself — route via the descriptions below.
- **nrev-build** — core protocol; load before touching any workflow tool
- **nrev-data** — one-off data pull, no workflow
- **node-settings** — load before configuring any node
- **workflow-examples**, **nrev-fix**
- **list-building**, **qualification-and-disqualification**, **research**,
  **content-generation**, **gtm-automations**, **nomination** — per objective

Never guess node settings field names or values — discover them live via
`find_node` → `describe_node`. Validate after every change with
`validate_workflow`.
