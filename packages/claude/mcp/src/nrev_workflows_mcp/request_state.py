"""Per-request identity seam for the hosted (streamable-http) transport.

The `mcp` SDK already resolves "who made this request" once per request, via
its own `AuthContextMiddleware` (installed automatically whenever a FastMCP
server is built with `auth_server_provider`/`auth` set) and a context var
(`mcp.server.auth.middleware.auth_context.auth_context_var`). We don't
duplicate that — we just read it through one seam so `auth.py`/`tenant.py`
have exactly one thing to check (and one thing to monkeypatch in tests)
instead of importing the SDK's auth-context machinery directly in two places.

On the stdio transport this always returns None — there is no HTTP request,
no auth middleware, and `auth.py`/`tenant.py` fall back to their existing
local-file/module-global behavior, unchanged.
"""
from __future__ import annotations

from typing import Optional


def hosted_identity() -> Optional[str]:
    """The current request's nRev session id (our `AccessToken.subject`), or
    None on the stdio transport / an unauthenticated custom route."""
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token
    except ImportError:
        return None
    token = get_access_token()
    return token.subject if token else None
