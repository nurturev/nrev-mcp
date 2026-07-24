"""FastMCP application instance.

Tool modules import `mcp` from here and register themselves with @mcp.tool().
The entrypoints (server.py for stdio, server_http.py for hosted) import the
tool modules for their side effects.

Whether `mcp` is built with OAuth wired in is decided once, at import time,
by the `NREV_TRANSPORT` env var — `auth_server_provider`/`auth` are
constructor-only args on FastMCP, so this can't be retrofitted after the
fact, and every `tools_*.py` module imports this single `mcp` singleton at
decoration time, so there's exactly one instance to get right, not two.
`server_http.py` sets `NREV_TRANSPORT=streamable-http` as its very first
statement, before importing this module, so the stdio entrypoint
(server.py) never needs to know this variable exists.
"""
import os

from mcp.server.fastmcp import FastMCP

_HOSTED = os.environ.get("NREV_TRANSPORT") == "streamable-http"

INSTRUCTIONS = """\
Tools for building and operating workflows on the nRev GTM platform, plus the
nRev tables service (lightweight database the workflows read/write).

Protocol for building workflows (the `building-workflows` skill has the full
version — load it when asked to build or edit a workflow):
1. Ensure the user is signed in: call get_auth_status; if unset/expired, call
   auth_login — it opens the user's browser to sign in once (auto-refreshes
   after). Don't surface environments, shell commands, or file paths to the
   user; just have them finish the browser sign-in.
2. Confirm the tenant: call get_active_tenant and tell the user which tenant the
   work will happen in (by name). A user may belong to several tenants and can
   switch the active one in the web app at any time — the active tenant is
   server-side state, NOT in the token, so the same session can start resolving
   to a different tenant mid-task. The first call anchors work to that tenant.
   This MCP never switches tenants itself; if the user wants a different one,
   ask them to switch in the web app, then call get_active_tenant again. If a
   later call reports changed_since_pin, or a tool stops with a "tenant changed"
   error, HALT — tell the user the tenant changed (from → to) and confirm how to
   proceed before doing anything else.
4. Check search_plays for an existing template before building from scratch.
5. Discover nodes: find_node(intent) to locate the right node by description,
   then describe_node to get its settings schema AND live dropdown options in
   one call. NEVER guess node settings field names or values — your training
   data does not contain this platform's field names, and wrong shapes fail
   silently. (See the workflow-examples skill for complete, correctly-shaped
   builds.)
6. Build with edit_workflow (batched operations) and update_node_settings.
7. validate_workflow after every batch of changes.
8. Test-run with run_workflow / run_node, then inspect get_execution and
   get_node_output — including row-level errors, which do NOT surface in the
   node-level status. (When a run fails, the troubleshooting skill maps
   symptoms to fixes.)
Executions consume tenant credits: keep nodes in test mode while iterating.
A full run_workflow with live nodes is refused without confirm=true — use
estimate_run_cost and get the user's go-ahead before spending real credits.

The tenant knowledge base holds the company's website, ICPs, personas,
competitors, and product offering — the context AI nodes draw on. Before
generating or personalising content, ground it: search_knowledge(query) for the
entries relevant to the task, or get_knowledge_base for the full picture plus
gaps. save_knowledge persists learnings back (reconciling add/update in one
call); forget_knowledge removes an entry.
"""

if _HOSTED:
    from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions

    from . import config
    from .oauth import NrevOAuthProvider, handle_webapp_callback

    _issuer = config.hosted_issuer_url()
    mcp = FastMCP(
        "nrev-workflows",
        instructions=INSTRUCTIONS,
        auth_server_provider=NrevOAuthProvider(),
        auth=AuthSettings(
            issuer_url=_issuer,
            resource_server_url=_issuer,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        ),
    )
    # The web app's relay POSTs here — deliberately unauthenticated (it's not
    # an MCP tool call, it's the OAuth handoff itself). See oauth.py.
    mcp.custom_route("/oauth/webapp-callback", methods=["POST", "OPTIONS"])(
        handle_webapp_callback
    )
else:
    mcp = FastMCP("nrev-workflows", instructions=INSTRUCTIONS)
