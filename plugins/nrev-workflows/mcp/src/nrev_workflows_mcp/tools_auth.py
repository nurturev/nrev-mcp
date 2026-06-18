"""Auth + account tools."""
from __future__ import annotations

from . import api, auth, config
from . import login as login_mod
from .app import mcp


@mcp.tool()
def auth_login() -> dict:
    """Sign the user in to nRev. Call this whenever authentication is needed —
    when get_auth_status is unset/expired, or any tool returns 401.

    It opens a sign-in page in the user's browser and completes on its own once
    they finish; the session then refreshes automatically, so the user signs in
    only once. Just call this tool, then tell the user to finish signing in in
    the browser window that opens.

    Do NOT ask the user which environment to use, and do NOT mention internal
    commands, file paths, or flags — those are deployment details, not user
    choices. If this call returns a timeout, the sign-in may still have
    completed: call get_auth_status to check before retrying.
    """
    try:
        return login_mod.login()
    except login_mod.LoginError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "hint": "Tell the user to complete the sign-in in the browser, then call get_auth_status.",
        }


@mcp.tool()
def set_jwt(token: str) -> dict:
    """Advanced/automation fallback for supplying a pre-issued token (held in
    memory only, not persisted). Prefer auth_login for normal sign-in — never ask
    an end user to fetch a token by hand. Use this only when a token is explicitly
    provided (e.g. CI). Accepts a bare token or a `Bearer <token>` value.
    """
    return auth.set_jwt(token)


@mcp.tool()
def get_auth_status(include_credits: bool = False) -> dict:
    """Check whether the user is signed in and when the session expires. Call at
    the start of a session before other tools; if it reports unset/expired, call
    auth_login. With include_credits=true, also returns the tenant's credit
    balance (useful before running workflows). Use the result to decide whether to
    sign in — no need to read the raw fields back to the user.
    """
    out = auth.status()
    out.setdefault("env", config.env_name())
    if include_credits and out.get("status") == "set":
        try:
            out["credit_balance"] = api.credit_balance()
        except Exception as exc:  # balance is a courtesy — auth status still useful
            out["credit_balance_error"] = str(exc)
    return out
