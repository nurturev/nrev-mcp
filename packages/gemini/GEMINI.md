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

## Spend safety

Executions consume tenant credits. Keep nodes in **test mode** while iterating.
A live `run_workflow` (any node not in test mode) is refused without
`confirm=true`; use `estimate_run_cost` and get the user's go-ahead before
spending real credits.

## Skills (loaded on demand)

- **building-workflows** — core protocol; load before touching any workflow tool
- **node-settings** — load before configuring any node
- **workflow-examples**, **troubleshooting**
- **list-building**, **qualification-and-disqualification**, **research**,
  **content-generation**, **gtm-automations**, **nomination** — per objective

Never guess node settings field names or values — discover them live via
`find_node` → `describe_node`. Validate after every change with
`validate_workflow`.
