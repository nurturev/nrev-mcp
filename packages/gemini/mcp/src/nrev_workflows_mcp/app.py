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

Pick the path BEFORE you build anything — there are two, and the light one is
usually right:
- ONE-OFF ("get me this now", a single pull, exploratory/consultant-style ask,
  no schedule): call list_data_tools and, if one covers the need, run it with
  run_data_tool. No workflow. This is the DEFAULT for one-off data questions.
- WORKFLOW (build): only when the user wants it to run REPEATEDLY, fire on a
  TRIGGER, or chain multiple steps — or when no data tool covers the need and
  the user agrees to the heavier build. Building a workflow to answer a one-off
  question wastes time and credits; don't default to it just because the ask
  involves data.
The data tools are SEED-BASED (they enrich/scrape a known domain, LinkedIn URL,
or company); list_data_tools is authoritative, so check it. If the ask is cold
people/company SEARCH ("find me founders in India") and nothing there matches,
that capability lives only in workflow search nodes — say so and let the user
choose to build a search workflow, rather than silently building one.

Protocol for building workflows (the `nrev-build` skill has the full
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
   node-level status. (When a run fails, the nrev-fix skill maps
   symptoms to fixes.)
Executions consume tenant credits: keep nodes in test mode while iterating.
A full run_workflow with live nodes is refused without confirm=true — use
estimate_run_cost and get the user's go-ahead before spending real credits.

One-off data mechanics: run_data_tool's first call (confirm=false) returns a
credit estimate instead of running, so show the user the cost and get their
go-ahead before re-calling with confirm=true. Persist results with
save_to_table, then OFFER to automate the pull as a workflow — don't assume the
user wants one built.

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
        # FastMCP.__init__ has hardcoded Python-level defaults for host/port
        # ("127.0.0.1", 8000) that it force-passes into its internal Settings
        # object — that shadows Settings' own FASTMCP_HOST/FASTMCP_PORT
        # env-var support entirely unless we pass them explicitly here.
        # Verified by actually running the server: with only the env vars
        # set (no explicit host=/port=), it silently bound to 127.0.0.1:8000
        # regardless — unreachable from outside the pod.
        host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("FASTMCP_PORT", "8000")),
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

    # Kubernetes probes — the mcp SDK has no built-in health route, and a
    # probe obviously shouldn't need an OAuth token, so these use
    # custom_route too (unauthenticated by design, per its own docstring).
    @mcp.custom_route("/healthCheck", methods=["GET"])
    async def _health_check(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok"})

    @mcp.custom_route("/readiness", methods=["GET"])
    async def _readiness(request):
        from starlette.responses import JSONResponse

        from . import session_store

        try:
            session_store.ping()
        except Exception as exc:
            return JSONResponse({"status": "not_ready", "error": str(exc)}, status_code=503)
        return JSONResponse({"status": "ready"})

else:
    mcp = FastMCP("nrev-workflows", instructions=INSTRUCTIONS)
