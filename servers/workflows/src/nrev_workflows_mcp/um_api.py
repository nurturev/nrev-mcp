"""Thin wrappers over the user-management (UM) Tenant Knowledge Base API.

A third host beyond the workflow and tables services. UM also backs session
refresh (see auth.py); these are its *data* endpoints. Same Supabase JWT — the
tenant is resolved server-side from the token, so a tenant id is never passed.

Verified against UM's ``tenant_knowledge_base`` domain. The KB is one website
field plus four entry collections (``KB_FIELD_TYPES``); each entry is
``{id, name, description, keywords, created_at, updated_at}``. Writes return the
affected entry and a ``completion`` summary; the full KB comes from get.
"""
from __future__ import annotations

from typing import Any, Optional

from . import config
from .transport import request as _request

# The four entry collections in the tenant knowledge base. `official_website` is
# a separate singular field, not an entry collection.
KB_FIELD_TYPES = (
    "company_icps",
    "ideal_personas",
    "identified_competitors",
    "product_offering",
)

_BASE = "/tenant/knowledge_base"


def request(method: str, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
    return _request(config.um_url(), method, path, json_body=json_body, params=params)


# ── tenancy ────────────────────────────────────────────────────────────────
# A user may belong to several tenants; exactly one is active server-side (the
# token alone doesn't pin it — UM resolves the active mapping per request). This
# lists all of them so the active one (is_enabled) can be identified.


def get_user_tenants() -> dict:
    """GET /user/tenants -> {"tenants": [{tenant_id, tenant_name, tenant_domain, is_enabled}]}."""
    return request("GET", "/user/tenants")


# ── knowledge base ───────────────────────────────────────────────────────────


def get_knowledge_base() -> dict:
    return request("GET", _BASE)


def update_website(value: str) -> dict:
    return request("POST", f"{_BASE}/website", json_body={"value": value})


def add_entry(field_type: str, name: str, description: str = "", keywords: str = "") -> dict:
    return request(
        "POST",
        f"{_BASE}/entry",
        json_body={
            "field_type": field_type,
            "name": name,
            "description": description,
            "keywords": keywords,
        },
    )


def update_entry(
    entry_id: str, field_type: str, name: str, description: str = "", keywords: str = ""
) -> dict:
    return request(
        "POST",
        f"{_BASE}/entry/update",
        json_body={
            "entry_id": entry_id,
            "field_type": field_type,
            "name": name,
            "description": description,
            "keywords": keywords,
        },
    )


def delete_entry(entry_id: str, field_type: str) -> Any:
    # field_type is a required query param on the UM route.
    return request("DELETE", f"{_BASE}/entry/{entry_id}", params={"field_type": field_type})
