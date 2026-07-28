"""Hosted entrypoint: runs the MCP server over streamable-http, with OAuth.

Deliberately a separate console script (`nrev-workflows-mcp-http`) from the
stdio entrypoint (`nrev-workflows-mcp`, server.py) — the hosted listener
should only ever start via an explicit, unambiguous command, not as a side
effect of an env var on the script every local/CLI user already runs.

Sets NREV_TRANSPORT before importing anything else in this package, since
app.py reads it at import time to decide whether to build FastMCP with
OAuth wired in (auth_server_provider/auth are constructor-only args — see
app.py's docstring for why this can't happen later).
"""
from __future__ import annotations

import os

os.environ.setdefault("NREV_TRANSPORT", "streamable-http")

from . import config  # noqa: E402
from .app import mcp  # noqa: E402

# Tool modules register themselves against `mcp` on import — same set as the
# stdio entrypoint (server.py). Keep this list in sync with server.py's —
# nothing enforces that automatically (a hosted-only file like this one isn't
# touched by changes made solely on the stdio side, so a merge can silently
# leave it stale; confirmed by a live tools/list count after this was first
# written without tools_data/tools_listeners).
from . import tools_auth  # noqa: F401,E402
from . import tools_tenant  # noqa: F401,E402
from . import tools_discovery  # noqa: F401,E402
from . import tools_workflows  # noqa: F401,E402
from . import tools_execution  # noqa: F401,E402
from . import tools_listeners  # noqa: F401,E402
from . import tools_tables  # noqa: F401,E402
from . import tools_data  # noqa: F401,E402
from . import tools_knowledge  # noqa: F401,E402
from . import tools_tags  # noqa: F401,E402


def main() -> None:
    # host/port are the mcp SDK's own Settings convention
    # (FASTMCP_HOST/FASTMCP_PORT, env_prefix="FASTMCP_") — set by the
    # Dockerfile, not read here directly.
    config.hosted_issuer_url()  # fail fast if misconfigured, before serving traffic
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
