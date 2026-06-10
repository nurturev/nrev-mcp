"""Auth + account tools."""
from __future__ import annotations

from . import api, auth
from .app import mcp


@mcp.tool()
def set_jwt(token: str) -> dict:
    """Store the user's nRev platform JWT for this session (in process memory
    only — never written to disk; lost on restart).

    Call when get_auth_status reports unset/expired, or any API call returns
    401. The user obtains the token from the platform web app: DevTools →
    Network → any API request → copy the Authorization header value (with or
    without the `Bearer ` prefix — both accepted). Tokens last ~12 hours.
    """
    return auth.set_jwt(token)


@mcp.tool()
def get_auth_status(include_credits: bool = False) -> dict:
    """Check whether a JWT is loaded and when it expires. Call at the start of
    a session before other tools.

    With include_credits=true, also fetches the tenant's credit balance —
    useful before running expensive workflows (every executed node row
    consumes credits).
    """
    out = auth.status()
    if include_credits and out.get("status") == "set":
        try:
            out["credit_balance"] = api.credit_balance()
        except Exception as exc:  # balance is a courtesy — auth status still useful
            out["credit_balance_error"] = str(exc)
    return out
