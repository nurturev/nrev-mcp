"""FastMCP application instance.

Tool modules import `mcp` from here and register themselves with @mcp.tool().
The entrypoint (server.py) imports the tool modules for their side effects.
"""
from mcp.server.fastmcp import FastMCP

INSTRUCTIONS = """\
Tools for building and operating workflows on the nRev GTM platform, plus the
nRev tables service (lightweight database the workflows read/write).

Protocol for building workflows (the `building-workflows` skill has the full
version — load it when asked to build or edit a workflow):
1. Ensure the user is signed in: call get_auth_status; if unset/expired, call
   auth_login — it opens the user's browser to sign in once (auto-refreshes
   after). Don't surface environments, shell commands, or file paths to the
   user; just have them finish the browser sign-in.
2. Check search_plays for an existing template before building from scratch.
3. Discover nodes: find_node(intent) to locate the right node by description,
   then describe_node to get its settings schema AND live dropdown options in
   one call. NEVER guess node settings field names or values — your training
   data does not contain this platform's field names, and wrong shapes fail
   silently. (See the workflow-examples skill for complete, correctly-shaped
   builds.)
4. Build with edit_workflow (batched operations) and update_node_settings.
5. validate_workflow after every batch of changes.
6. Test-run with run_workflow / run_node, then inspect get_execution and
   get_node_output — including row-level errors, which do NOT surface in the
   node-level status. (When a run fails, the troubleshooting skill maps
   symptoms to fixes.)
Executions consume tenant credits: keep nodes in test mode while iterating.
A full run_workflow with live nodes is refused without confirm=true — use
estimate_run_cost and get the user's go-ahead before spending real credits.
"""

mcp = FastMCP("nrev-workflows", instructions=INSTRUCTIONS)
