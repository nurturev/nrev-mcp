"""Tenant awareness — know (and stay on) the right tenant.

A multi-tenant user has one *active* tenant at a time, resolved server-side from
their session rather than baked into the token. If they switch it in the web app
mid-session, the same token starts resolving to the new tenant — so work can
silently cross tenants. This surfaces the active tenant and anchors work to it;
drift is then caught automatically (creation guards + access-failure diagnosis,
see tenant.py / transport.py). We never switch the tenant ourselves — that stays
a user action in the web app.
"""
from __future__ import annotations

from . import tenant
from .app import mcp


@mcp.tool()
def get_active_tenant(repin: bool = False) -> dict:
    """Report the tenant you're operating on, plus the tenants the user can
    switch among. Call this before starting tenant-scoped work — building or
    editing workflows or tables, reading or writing the knowledge base — and tell
    the user which tenant (by name) the work will happen in.

    The first call *pins* the active tenant as the one this session's work is
    anchored to. Later calls report `changed_since_pin: true` if the user
    switched tenant in the web app since then — when that happens, STOP, tell the
    user the tenant changed, and confirm how to proceed before doing more work.

    This MCP never switches tenants itself; to change the active tenant the user
    switches it in the web app, then you call get_active_tenant again. Pass
    repin=true only to deliberately re-anchor to the now-active tenant after the
    user confirms they intend to work there.

    Returns: active {tenant_id, tenant_name, tenant_domain}, the pinned tenant,
    changed_since_pin, available (all the user's tenants, each flagged
    is_active), and can_switch."""
    info = tenant.active_tenant(force=True)
    active = info.get("active")
    available = info.get("available", [])

    prior = tenant.pinned()
    changed = bool(
        prior is not None and active is not None
        and active.get("tenant_id") != prior.get("tenant_id")
    )

    if prior is None or repin:
        tenant.pin(active)
        changed = False

    return {
        "active": active,
        "pinned": tenant.pinned(),
        "changed_since_pin": changed,
        "available": available,
        "can_switch": sum(1 for t in available if t.get("tenant_id") is not None) > 1,
        "note": (
            "Work is anchored to the pinned tenant. This tool never switches "
            "tenants — to change it the user switches in the web app, then call "
            "get_active_tenant again. If changed_since_pin is true, stop and "
            "confirm with the user before continuing."
        ),
    }
