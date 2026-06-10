"""Entrypoint: registers all tools and runs the MCP server over stdio."""
from __future__ import annotations

from . import auth
from .app import mcp

# Tool modules register themselves against `mcp` on import.
from . import tools_auth  # noqa: F401,E402
from . import tools_discovery  # noqa: F401,E402
from . import tools_workflows  # noqa: F401,E402
from . import tools_execution  # noqa: F401,E402
from . import tools_tables  # noqa: F401,E402


def main() -> None:
    auth.seed_from_env()
    mcp.run()


if __name__ == "__main__":
    main()
