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
1. Ensure auth (get_auth_status; set_jwt if unset/expired).
2. Check search_plays for an existing template before building from scratch.
3. Discover nodes with search_nodes; read exact settings schemas with
   get_node_type and live dropdown values with get_field_options. NEVER guess
   node settings field names or values — your training data does not contain
   this platform's field names, and wrong shapes fail silently.
4. Build with edit_workflow (batched operations) and update_node_settings.
5. validate_workflow after every batch of changes.
6. Test-run with run_workflow / run_node, then inspect get_execution and
   get_node_output — including row-level errors, which do NOT surface in the
   node-level status.
Executions consume tenant credits: keep nodes in test mode while iterating.
"""

mcp = FastMCP("nrev-workflows", instructions=INSTRUCTIONS)
