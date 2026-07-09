"""Tenant Knowledge Base (TKB) tools — the company context AI nodes draw on.

The knowledge base lives in user-management (a different host from the workflow
and tables APIs; same session token, tenant resolved server-side). It holds the
tenant's official website plus four entry collections — company ICPs, ideal
personas, identified competitors, and product offering. AI workflow nodes (Ask
AI, content generation) reference this, so reading it keeps generated content
grounded in the company's real positioning; writing persists what you learn back
to the source the platform already uses.

These are task-oriented, not a 1:1 mirror of the UM CRUD routes:
  - search_knowledge  — pull only the entries relevant to a task (ranked)
  - get_knowledge_base — full read, annotated with gaps + usability
  - save_knowledge    — reconciling merge upsert (add/update in one call, safely)
  - forget_knowledge  — guarded delete that surfaces live-workflow blocks
"""
from __future__ import annotations

from typing import Optional

from . import ranking, um_api
from .app import mcp
from .transport import APIError

_FIELD_TYPES_HELP = ", ".join(um_api.KB_FIELD_TYPES)


def _data(raw):
    """Unwrap the UM ``{"data": ..., "message": ...}`` envelope."""
    return raw.get("data", raw) if isinstance(raw, dict) else raw


def _check_field_type(field_type: str) -> None:
    if field_type not in um_api.KB_FIELD_TYPES:
        raise ValueError(f"field_type must be one of: {_FIELD_TYPES_HELP}")


def _flatten(data: dict, field_types) -> list[dict]:
    """All entries across the given collections, each tagged with its collection."""
    out: list[dict] = []
    for ft in field_types:
        for entry in data.get(ft) or []:
            if isinstance(entry, dict):
                out.append({"collection": ft, **entry})
    return out


def _annotate_gaps(data: dict) -> dict:
    """Derive which of the five KB slots are empty and whether it's usable.

    Mirrors UM's own filledness rule (usable once >= 3 of 5 slots are filled).
    """
    missing: list[str] = []
    website = (data.get("official_website") or {}).get("value")
    if not (website and str(website).strip()):
        missing.append("official_website")
    for ft in um_api.KB_FIELD_TYPES:
        if not data.get(ft):
            missing.append(ft)
    filled = (data.get("completion") or {}).get("filled_count", 0)
    return {"gaps": missing, "is_usable": filled >= 3}


@mcp.tool()
def search_knowledge(
    query: str = "", field_types: Optional[list[str]] = None, limit: int = 10
) -> dict:
    """Pull the knowledge base entries most relevant to a task, instead of
    dumping the whole KB into context. Use this to ground a specific piece of
    work — e.g. before personalising outreach for "fintech CFOs", search that to
    get the matching ICP/persona/product entries.

    `query` is natural language, matched (synonym-aware) across entry names,
    descriptions, and keywords. `field_types` optionally scopes the search to
    some collections (company_icps, ideal_personas, identified_competitors,
    product_offering) — e.g. ["ideal_personas"] to list personas only. With an
    empty `query`, returns all entries (filtered by `field_types`), so it doubles
    as "list the personas". Results carry their `collection` and `id` (use those
    with save_knowledge / forget_knowledge)."""
    types = field_types or list(um_api.KB_FIELD_TYPES)
    for ft in types:
        _check_field_type(ft)

    data = _data(um_api.get_knowledge_base())
    entries = _flatten(data, types)

    if not query.strip():
        return {"query": query, "results": entries[: max(0, int(limit))]}

    # ranking.rank scores over {name, category, description}; fold keywords into
    # the scored text via a throwaway item that points back to the clean entry,
    # so the returned entries stay unpolluted.
    rank_items = [
        {
            "name": e.get("name", ""),
            "category": e.get("collection", ""),
            "description": f'{e.get("description", "")} {e.get("keywords", "")}'.strip(),
            "_idx": i,
        }
        for i, e in enumerate(entries)
    ]
    ranked = ranking.rank(query, rank_items, limit=int(limit))
    results = [{**entries[r["_idx"]], "score": r["_score"]} for r in ranked]
    return {"query": query, "results": results}


@mcp.tool()
def get_knowledge_base() -> dict:
    """Read the full tenant knowledge base — the company context the platform's
    AI nodes draw on: the official website plus four entry collections
    (company_icps, ideal_personas, identified_competitors, product_offering).

    Use search_knowledge instead when you only need entries relevant to a
    specific task. This full read is annotated with `gaps` (which of the five
    slots are still empty) and `is_usable` (the platform treats the KB as usable
    once >= 3 of 5 are filled), so you can tell the user what's missing and
    offer to fill it. Each entry includes its `id` for later save/forget calls."""
    data = _data(um_api.get_knowledge_base())
    if isinstance(data, dict):
        data.update(_annotate_gaps(data))
    return data


@mcp.tool()
def save_knowledge(
    website: Optional[str] = None,
    company_icps: Optional[list[dict]] = None,
    ideal_personas: Optional[list[dict]] = None,
    identified_competitors: Optional[list[dict]] = None,
    product_offering: Optional[list[dict]] = None,
) -> dict:
    """Persist what you've learned about the company to the knowledge base — one
    entry or a whole research pass, in a single call.

    Pass `website` (a URL) and/or any of the four collections as lists of
    entries: `[{"name": ..., "description"?: ..., "keywords"?: ..., "id"?: ...}]`.
    `name` is required per entry. Each entry is reconciled, not blindly inserted:
      - matched to an existing entry by `id`, else by case-insensitive `name`
        -> UPDATE (omitted description/keywords keep their current values);
      - otherwise -> ADD (subject to the per-collection cap; over-cap adds are
        reported as `skipped`, not fatal).
    It never deletes — so it can't silently break a live workflow that depends on
    an entry (use forget_knowledge for removals). Returns a per-collection diff
    plus the resulting completion/gaps."""
    provided = {
        "company_icps": company_icps,
        "ideal_personas": ideal_personas,
        "identified_competitors": identified_competitors,
        "product_offering": product_offering,
    }
    if website is None and all(v is None for v in provided.values()):
        raise ValueError("pass website and/or at least one collection of entries to save")

    current = _data(um_api.get_knowledge_base())
    changes: dict = {}

    website_status = "unchanged"
    if website is not None:
        um_api.update_website(website)
        website_status = "updated"

    for collection, entries in provided.items():
        if entries is None:
            continue
        existing = current.get(collection) or []
        by_id = {str(e.get("id")): e for e in existing if isinstance(e, dict)}
        by_name = {
            (e.get("name") or "").strip().lower(): e
            for e in existing
            if isinstance(e, dict) and (e.get("name") or "").strip()
        }
        added, updated, skipped = [], [], []
        for entry in entries:
            name = (entry.get("name") or "").strip()
            if not name:
                skipped.append({"entry": entry, "error": "name is required"})
                continue
            pid = entry.get("id")
            match = by_id.get(str(pid)) if pid else by_name.get(name.lower())
            try:
                if match:
                    eid = str(match.get("id"))
                    desc = entry.get("description")
                    kw = entry.get("keywords")
                    um_api.update_entry(
                        eid,
                        collection,
                        name,
                        desc if desc is not None else match.get("description", ""),
                        kw if kw is not None else match.get("keywords", ""),
                    )
                    updated.append(name)
                else:
                    um_api.add_entry(
                        collection, name, entry.get("description") or "", entry.get("keywords") or ""
                    )
                    added.append(name)
            except APIError as exc:
                skipped.append({"name": name, "error": exc.body[:200]})
        changes[collection] = {"added": added, "updated": updated, "skipped": skipped}

    final = _data(um_api.get_knowledge_base())
    out = {"website": website_status, "changes": changes, "completion": final.get("completion")}
    out.update(_annotate_gaps(final))
    return out


@mcp.tool()
def forget_knowledge(entry_id: str, field_type: str, confirm: bool = False) -> dict:
    """Delete a knowledge base entry. Destructive — requires confirm=true, and
    you should name the entry to the user first (get the id from
    search_knowledge / get_knowledge_base).

    The platform refuses to delete an entry that a LIVE workflow still
    references. When that happens this returns `{"deleted": false,
    "blocked_by_live_workflow_ids": [...]}` rather than erroring — unpublish
    those workflows (set_workflow_live false) and retry."""
    if not confirm:
        raise ValueError("forget_knowledge is destructive — call again with confirm=true")
    _check_field_type(field_type)
    try:
        result = _data(um_api.delete_entry(entry_id, field_type))
    except APIError as exc:
        import json

        body = {}
        try:
            body = json.loads(exc.body)
        except Exception:
            pass
        details = body.get("details") or {}
        if details.get("live_workflow_ids") is not None or "live_workflow" in str(body):
            return {
                "deleted": False,
                "reason": body.get("error", "blocked_by_live_workflow"),
                "blocked_by_live_workflow_ids": details.get("live_workflow_ids", []),
                "hint": "Unpublish those workflows (set_workflow_live false), then retry.",
            }
        raise
    out: dict = {"deleted": entry_id}
    if isinstance(result, dict):
        out.update(result)
    return out
