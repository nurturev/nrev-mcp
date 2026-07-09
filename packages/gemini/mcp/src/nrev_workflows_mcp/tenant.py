"""Active-tenant tracking and drift detection.

A user can belong to several tenants; exactly one is *active* at a time, and that
is pure server-side state — the session token doesn't encode it (UM resolves the
active mapping on every request). So the same token can start resolving to a
different tenant if the user switches in the web app mid-session, out of band,
and work would silently cross tenants.

This module:
  - reads the active tenant from UM (`active_tenant`), lightly TTL-cached so
    guards and reads don't hammer UM;
  - lets a unit of work *pin* the tenant it's anchored to (`pin`/`pinned`),
    set by the get_active_tenant tool;
  - detects drift between the pinned tenant and the live one (`check_drift`) and
    hard-stops creation paths via `assert_pinned_active`.

We never switch the tenant — that stays a user action in the web app.
"""
from __future__ import annotations

import time
from typing import Optional

from . import um_api
from .transport import TenantChangedError

# Active-tenant reads are cached this long so back-to-back guards/reads don't
# each round-trip to UM; drift checks pass force=True to bypass it.
_CACHE_TTL_SECONDS = 10.0

_cache: Optional[dict] = None
_cache_at: float = 0.0

# The tenant this session's work is anchored to (set by get_active_tenant).
_pinned: Optional[dict] = None


def _unwrap_tenants(raw) -> list[dict]:
    """Pull the tenant list out of the UM response (tolerant of a data envelope)."""
    tenants = raw
    if isinstance(raw, dict):
        data = raw.get("data", raw)
        tenants = data.get("tenants", data) if isinstance(data, dict) else data
    return [t for t in (tenants or []) if isinstance(t, dict)]


def _slim(t: Optional[dict]) -> Optional[dict]:
    if not t:
        return None
    return {
        "tenant_id": t.get("tenant_id"),
        "tenant_name": t.get("tenant_name"),
        "tenant_domain": t.get("tenant_domain"),
    }


def active_tenant(force: bool = False) -> dict:
    """The currently-active tenant plus all the tenants the user can switch among.

    Returns ``{"active": {...}|None, "available": [{..., "is_active": bool}]}``.
    Lightly cached (TTL); ``force`` bypasses the cache for a fresh read.
    """
    global _cache, _cache_at
    now = time.time()
    if not force and _cache is not None and (now - _cache_at) < _CACHE_TTL_SECONDS:
        return _cache
    tenants = _unwrap_tenants(um_api.get_user_tenants())
    active = next((t for t in tenants if t.get("is_enabled")), None)
    out = {
        "active": _slim(active),
        "available": [{**_slim(t), "is_active": bool(t.get("is_enabled"))} for t in tenants],
    }
    _cache, _cache_at = out, now
    return out


def pinned() -> Optional[dict]:
    """The tenant work is currently anchored to (or None if nothing pinned)."""
    return _pinned


def pin(tenant: Optional[dict]) -> Optional[dict]:
    """Anchor subsequent work to ``tenant`` (a slim ``{tenant_id, ...}`` dict)."""
    global _pinned
    _pinned = _slim(tenant)
    return _pinned


def reset() -> None:
    """Drop the pin and cache (test isolation / logout)."""
    global _pinned, _cache, _cache_at
    _pinned = None
    _cache, _cache_at = None, 0.0


def check_drift(force: bool = True) -> Optional[dict]:
    """If a pin is set and the live active tenant differs, describe the drift.

    Returns ``None`` when nothing is pinned or the active tenant still matches;
    otherwise ``{"from": <pinned>, "to": <live>}``. ``force`` re-reads UM by
    default so a guard sees the truth rather than a stale cache.
    """
    if _pinned is None:
        return None
    live = active_tenant(force=force).get("active")
    live_id = live.get("tenant_id") if live else None
    if live_id == _pinned.get("tenant_id"):
        return None
    return {"from": _pinned, "to": live}


def assert_pinned_active(operation: str = "this operation") -> None:
    """Raise ``TenantChangedError`` if the active tenant drifted from the pin.

    Called at *creation* points (create_workflow / create_table) where the
    backend can't catch a mid-flight switch — a brand-new resource would silently
    land in the now-active tenant. No-op when nothing is pinned.
    """
    drift = check_drift(force=True)
    if drift is not None:
        raise TenantChangedError(drift["from"], drift["to"], operation)
