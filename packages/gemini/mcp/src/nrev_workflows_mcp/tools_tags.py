"""Tag catalog tools — the shared, tenant-scoped tag pool workflows and tables
both draw their tag_ids from (owned by user-management, a different host from
the workflow and tables APIs; same session token, tenant resolved server-side).

Assigning/detaching a tag on a specific workflow or table lives in
tools_workflows.py (set_workflow_tags / add_workflow_tag / remove_workflow_tag)
and tools_tables.py (set_table_tags / add_table_tag / remove_table_tag) — this
module only manages the catalog itself: what tags exist, their name/color.
"""
from __future__ import annotations

import json
from typing import Optional

from . import um_api
from .app import mcp
from .transport import APIError

_DEFAULT_COLOR = "94a3b8"  # neutral slate — used when the caller doesn't care


def _data(raw):
    return raw.get("data", raw) if isinstance(raw, dict) else raw


@mcp.tool()
def list_tags() -> dict:
    """List every tag in the tenant's catalog (id, name, color), sorted by
    name. Use to find an existing tag's id before calling set_workflow_tags /
    set_table_tags / add_workflow_tag / add_table_tag — or to show the user
    what tags already exist before creating a near-duplicate."""
    raw = _data(um_api.list_tags())
    return {"tags": raw if isinstance(raw, list) else []}


@mcp.tool()
def create_tag(name: str, color: Optional[str] = None) -> dict:
    """Create a new tag in the tenant's catalog. `color` is a 6-hex-digit
    string without '#' (defaults to a neutral gray if omitted).

    Fails if a tag with this name already exists (case-insensitive) — use
    find_or_create_tag instead if you just want "the tag named X, creating it
    if needed" without caring whether it's new."""
    return _data(um_api.create_tag(name, color or _DEFAULT_COLOR))


@mcp.tool()
def find_or_create_tag(name: str, color: Optional[str] = None) -> dict:
    """The tag named `name` in the tenant's catalog — returned as-is if it
    already exists (case-insensitive match), else created with `color`
    (defaults to a neutral gray). This is what "tag this workflow/table
    'high-value'" should call first to turn a name into a tag_id, since
    set_workflow_tags / set_table_tags only take ids.
    """
    existing = _data(um_api.list_tags())
    if isinstance(existing, list):
        for tag in existing:
            if isinstance(tag, dict) and (tag.get("name") or "").strip().lower() == name.strip().lower():
                return tag
    try:
        return _data(um_api.create_tag(name, color or _DEFAULT_COLOR))
    except APIError as exc:
        if exc.status_code != 409:
            raise
        # Lost a race with a concurrent create — the 409 body carries the
        # winner's id (details.existing_tag_id); fetch it via a fresh list
        # rather than trust a partially-committed error body across services.
        try:
            body = json.loads(exc.body)
            existing_id = (body.get("details") or {}).get("existing_tag_id")
        except Exception:
            existing_id = None
        refreshed = _data(um_api.list_tags())
        if isinstance(refreshed, list):
            for tag in refreshed:
                if isinstance(tag, dict) and (
                    tag.get("id") == existing_id
                    or (tag.get("name") or "").strip().lower() == name.strip().lower()
                ):
                    return tag
        raise


@mcp.tool()
def update_tag(tag_id: str, name: Optional[str] = None, color: Optional[str] = None) -> dict:
    """Rename and/or recolor a catalog tag. This changes the tag EVERYWHERE
    it's applied (one canonical tag per name per tenant) — it does not create
    a new tag or affect which workflows/tables carry it."""
    return _data(um_api.update_tag(tag_id, name=name, color=color))
