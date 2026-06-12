"""Catalog discovery tools: node types, field options, connections, plays."""
from __future__ import annotations

import uuid
from typing import Optional

from . import api, projections, ranking
from .app import mcp


def _node_detail(
    node_definition_id: str,
    workflow_id: Optional[str] = None,
    node_id: Optional[str] = None,
    changed_field: Optional[str] = None,
) -> dict:
    """Slim settings schema + (when a node context is supplied) the
    settings-dependent `materialized_fields`. Shared by get_node_type and
    describe_node."""
    detail = projections.slim_definition_detail(api.get_node_definition(node_definition_id))
    if workflow_id and node_id:
        wf = api.get_workflow(workflow_id)
        block = next((b for b in (wf.get("blocks") or []) if b.get("id") == node_id), None)
        if block is None:
            detail["materialized_fields_error"] = f"node {node_id} not in workflow {workflow_id}"
        else:
            sfv = block.get("settings_field_values") or []
            trigger = changed_field or (sfv[0].get("field_name") if sfv else "")
            try:
                cfg = api.updated_node_config(node_id, node_definition_id, trigger, sfv)
                fields = ((cfg or {}).get("nodeDefinition") or {}).get("fields") or []
                detail["materialized_fields"] = [
                    {"name": f.get("name"), "label": f.get("label"), "type": f.get("type"), "required": f.get("required")}
                    for f in fields
                ]
            except Exception as exc:
                detail["materialized_fields_error"] = str(exc)
    return detail


@mcp.tool()
def search_nodes(
    query: Optional[str] = None,
    category: Optional[str] = None,
    only_triggers: bool = False,
    only_actions: bool = False,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """Search the catalog of available node types. Call BEFORE adding any node
    to a workflow — node availability and IDs must come from here, never from
    memory.

    `query` matches name/description (e.g. "enrich person", "google sheets",
    "slack message"). `only_triggers=true` filters to nodes that can start a
    workflow; `only_actions=true` to nodes that need a parent. Returns compact
    entries — follow up with get_node_type(node_definition_id) for the full
    settings schema of a candidate.
    """
    raw = api.list_node_definitions(
        limit=limit,
        offset=offset,
        search=query,
        category=category,
        only_trigger=only_triggers,
        only_action=only_actions,
    )
    data = raw.get("data") if isinstance(raw, dict) else raw
    items = [projections.slim_definition_item(d) for d in (data or [])]
    out: dict = {"nodes": items}
    if isinstance(raw, dict) and raw.get("meta"):
        out["meta"] = raw["meta"]
    return out


@mcp.tool()
def get_node_type(
    node_definition_id: str,
    workflow_id: Optional[str] = None,
    node_id: Optional[str] = None,
    changed_field: Optional[str] = None,
) -> dict:
    """Get a node type's full settings schema, AI metadata (capabilities and
    constraints), and expected output columns. Call before configuring any
    node — settings field names are NOT guessable (Pipedream connection fields
    especially: `...-gmail`, `...-conversations`, `...-googleSheets_connection_id`
    — no consistent pattern).

    For Pipedream-backed nodes whose schema depends on current settings (e.g.
    the worksheet dropdown materializes only after a sheet is picked), pass
    workflow_id + node_id of an existing node: the tool additionally returns
    `materialized_fields` computed from that node's current settings.
    `changed_field` names the setting you just changed (defaults to the first
    one present).

    For a node you're configuring fresh, prefer describe_node — it returns this
    schema AND pre-fetches every dropdown's live options in one call.
    """
    return _node_detail(node_definition_id, workflow_id, node_id, changed_field)


@mcp.tool()
def describe_node(
    node_definition_id: str,
    settings: Optional[dict] = None,
    workflow_id: Optional[str] = None,
    node_id: Optional[str] = None,
    include_options: bool = True,
    max_option_fields: int = 12,
) -> dict:
    """One-shot node configuration brief: the settings schema, AI metadata,
    expected outputs, AND the live dropdown options for every endpoint-backed
    field — collapsing what would otherwise be get_node_type + one
    get_field_options call per dropdown into a single round trip. Reach for this
    (over get_node_type) whenever you're about to configure a node.

    `settings` ({field_name: value}) feeds cascading dropdowns whose options
    depend on a prerequisite field (worksheet needs the sheet picked first; most
    Pipedream dropdowns need the connection set) — pass what you know so far.
    `workflow_id`+`node_id` additionally materialize settings-dependent fields
    (same as get_node_type). Options are fetched for up to `max_option_fields`
    fields; each field gains `options` (or `options_error` if the live fetch
    needed prerequisites you haven't supplied — set them and call again).
    """
    detail = _node_detail(node_definition_id, workflow_id, node_id)
    if not include_options:
        return detail

    settings_list = [{"field_name": k, "field_value": v} for k, v in (settings or {}).items()]
    by_name = {f.get("name"): f for f in detail.get("settings_fields") or []}
    fetched = 0
    for field_name in projections.fields_needing_options(detail):
        if fetched >= max(0, int(max_option_fields)):
            break
        fetched += 1
        field = by_name.get(field_name)
        if field is None:
            continue
        try:
            opts = api.field_options(str(uuid.uuid4()), node_definition_id, field_name, settings_list)
            data = opts.get("data") if isinstance(opts, dict) else opts
            field["options"] = data
            field.pop("dynamic_options_via", None)
        except Exception as exc:
            field["options_error"] = str(exc)
    return detail


@mcp.tool()
def find_node(
    intent: str,
    limit: int = 10,
    only_triggers: bool = False,
    only_actions: bool = False,
) -> dict:
    """Find node types by natural-language INTENT — "send a message on Slack",
    "join two lists", "enrich a company", "run on a schedule". Ranks the whole
    catalog by relevance (synonym-aware) rather than substring, so it surfaces
    the right node even when its name doesn't contain your words. Use this as
    the first move when you don't already know which node you need; fall back to
    search_nodes for exact keyword/category browsing.

    Returns the top matches as slim entries with a relevance `_score`. Confirm a
    candidate with get_node_type / describe_node before adding it.
    """
    items: list[dict] = []
    offset = 0
    for _ in range(3):  # sweep up to ~300 definitions; the catalog is small
        raw = api.list_node_definitions(
            limit=100, offset=offset, only_trigger=only_triggers, only_action=only_actions
        )
        data = raw.get("data") if isinstance(raw, dict) else raw
        page = [projections.slim_definition_item(d) for d in (data or [])]
        items.extend(page)
        if len(page) < 100:
            break
        offset += 100
    return {"nodes": ranking.rank(intent, items, limit), "catalog_size_scanned": len(items)}


@mcp.tool()
def get_field_options(
    node_definition_id: str,
    field_name: str,
    settings: Optional[dict] = None,
    search: Optional[str] = None,
    node_id: Optional[str] = None,
) -> dict:
    """Fetch the live dropdown options for one node settings field — Slack
    channels, spreadsheets, worksheets, table columns, AI model names, etc.
    Use the option's `value` in settings and its `label` as the field label.
    Never invent dropdown values.

    `settings`: current/prerequisite settings as {field_name: value} — required
    for cascading dropdowns (worksheet needs the sheet picked first; most
    Pipedream dropdowns need the connection field set). `node_id` is optional
    (used for logging only — a placeholder UUID is fine and the node need not
    exist yet). `search` narrows large option lists server-side.
    """
    settings_list = [{"field_name": k, "field_value": v} for k, v in (settings or {}).items()]
    return api.field_options(
        node_id or str(uuid.uuid4()),
        node_definition_id,
        field_name,
        settings_list,
        search=search,
    )


@mcp.tool()
def list_connections(connection_app_id: Optional[str] = None, apps_search: Optional[str] = None) -> dict:
    """List OAuth connections (Gmail, Slack, Sheets, HubSpot…) available for
    app-backed nodes, or search the catalog of connectable apps.

    Modes:
    - no args → the current user's own connections.
    - connection_app_id → ALL connections in the tenant for that app,
      including teammates' (what the UI's connection picker shows). This is
      how you find a usable connection_id in multi-user tenants.
    - apps_search → search the catalog of connectable apps; returns each
      app's connection_app_id to use with the mode above.

    Note: a teammate's connection may work for some apps (Gmail, Sheets) but
    fail at runtime for others (Google Calendar) — prefer the user's own.
    """
    if apps_search:
        raw = api.list_connection_apps(search=apps_search)
        return {"apps": raw.get("data", raw)}
    return {"connections": api.list_connections(connection_app_id)}


@mcp.tool()
def search_plays(query: Optional[str] = None, limit: int = 10) -> dict:
    """Search plays — published workflow templates. ALWAYS check here before
    building a workflow from scratch: summoning a play that matches the user's
    objective and adapting it is faster and less error-prone than assembling
    nodes manually.

    To instantiate one, call create_workflow(from_play_id=...). Returns play
    summaries (playId, name, description, categories, playbook).
    """
    # The catalog is served by GET /playbooks, which groups plays into
    # playbooks and embeds each playbook's plays inline. Flatten back to a
    # single list of plays (the unit create_workflow/summon operates on),
    # tagging each with its parent playbook for context and de-duping plays
    # that appear in more than one playbook.
    raw = api.list_playbooks(search=query, limit=limit)
    playbooks = raw.get("data") if isinstance(raw, dict) else (raw or [])
    plays: list[dict] = []
    seen: set[str] = set()
    for pb in playbooks or []:
        pb_name = pb.get("name")
        for play in pb.get("plays") or []:
            pid = play.get("playId")
            if pid in seen:
                continue
            seen.add(pid)
            plays.append({**play, "playbook": pb_name})
    return {"plays": plays}
