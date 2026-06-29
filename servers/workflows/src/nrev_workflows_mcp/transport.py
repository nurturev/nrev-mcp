"""Shared HTTP plumbing for the workflow and tables APIs.

One place to inject the JWT header (and the `X-Nrev-Client` client-source
header) and to surface API errors as a typed exception carrying the URL +
response body, so every tool failure is actionable for the calling agent.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from . import auth, config

TIMEOUT_SECONDS = float(os.environ.get("NREV_TIMEOUT", "60"))

# Client-source attribution for prod-alert triage. Every backend call from this
# MCP server (Claude Code / Cowork / Claude.ai -> nRev platform) carries this so
# the backend attributes the traffic deterministically instead of guessing from
# the python-httpx user-agent (which its heuristic would map to `nrev-lite`).
# Observability signal only -- never used for authz. Parallel to gtm-engine's
# NrvClient (`nrev-lite`) and its MCP server (`nrev-lite-mcp`).
CLIENT_SOURCE = "nrev-mcp"


class APIError(RuntimeError):
    def __init__(self, status_code: int, body: str, url: str):
        super().__init__(f"HTTP {status_code} from {url}: {body[:500]}")
        self.status_code = status_code
        self.body = body
        self.url = url


def _tenant_label(t: Optional[dict]) -> str:
    t = t or {}
    name, tid = t.get("tenant_name"), t.get("tenant_id")
    if name and tid is not None:
        return f"{name} (id {tid})"
    if tid is not None:
        return f"tenant {tid}"
    return name or "none"


class TenantChangedError(RuntimeError):
    """The active tenant changed out-of-band (the user switched it) mid-operation.

    A user can belong to several tenants; the active one is server-side state, not
    encoded in the token — so the same session can start resolving to a different
    tenant if the user switches in the web app. Raised so the agent stops and
    informs the user instead of silently acting on the wrong tenant. We never
    switch the tenant ourselves.
    """

    def __init__(self, from_tenant: Optional[dict], to_tenant: Optional[dict], operation: str = "this operation"):
        self.from_tenant = from_tenant or {}
        self.to_tenant = to_tenant or {}
        self.operation = operation
        super().__init__(
            f"Active tenant changed from {_tenant_label(self.from_tenant)} to "
            f"{_tenant_label(self.to_tenant)} before {operation}. Stopped so nothing "
            "lands in the wrong tenant. Tell the user the tenant changed and confirm "
            "how to proceed — they can switch back in the web app, or you can re-anchor "
            "to the new tenant with get_active_tenant(repin=true) if they intend to "
            "work there."
        )


def _maybe_raise_tenant_changed(host: str, status_code: int) -> None:
    """On an access failure from a resource host, surface a tenant switch as the
    cause (if that's what it is) instead of a confusing 403/404.

    A mid-session tenant switch makes the now-active tenant unable to see the
    resource a call targets, so the backend rejects it. We only diagnose the
    workflow/tables hosts — never the UM host, since the drift check itself calls
    UM and a UM failure isn't a tenant switch (this also prevents recursion)."""
    if status_code not in (403, 404):
        return
    if host.rstrip("/") == config.um_url().rstrip("/"):
        return
    try:
        from . import tenant

        drift = tenant.check_drift(force=True)
    except Exception:
        return
    if drift:
        raise TenantChangedError(drift["from"], drift["to"], "this request")


def _send(
    host: str,
    token: str,
    method: str,
    path: str,
    json_body: Optional[dict],
    params: Optional[dict],
) -> httpx.Response:
    with httpx.Client(
        base_url=host,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # Client-source attribution for prod-alert triage (observability only).
            "X-Nrev-Client": CLIENT_SOURCE,
        },
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
    ) as client:
        return client.request(method, path, json=json_body, params=params)


def request(
    host: str,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    token = auth.get_jwt()
    r = _send(host, token, method, path, json_body, params)

    # On a 401 the session token may have expired between the expiry check and
    # the request. Force a refresh and retry once. (A manual override has no
    # refresh token, so force_refresh returns None and we surface the 401.)
    if r.status_code == 401:
        new_token = auth.force_refresh()
        if new_token and new_token != token:
            r = _send(host, new_token, method, path, json_body, params)

    if r.status_code >= 400:
        _maybe_raise_tenant_changed(host, r.status_code)
        raise APIError(r.status_code, r.text, str(r.request.url))
    if not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return r.text
