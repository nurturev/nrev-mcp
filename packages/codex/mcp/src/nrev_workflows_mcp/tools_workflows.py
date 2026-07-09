"""Workflow CRUD and mutation tools."""
from __future__ import annotations

from typing import Any, Optional

from . import api, projections, shapes, tenant
from .app import mcp

_def_cache: dict[str, dict] = {}


def _lookup_node_def(type_id: str) -> dict:
    if type_id not in _def_cache:
        raw = api.get_node_definition(type_id)
        _def_cache[type_id] = {
            "is_trigger": bool(raw.get("is_trigger") or raw.get("isTrigger")),
            "is_listener": bool(raw.get("is_listener") or raw.get("isListener")),
            "value": raw.get("value") or raw.get("node_type_id"),
            "name": raw.get("name") or raw.get("node_type_name"),
        }
    return _def_cache[type_id]


def _persist(wf: dict, result: dict) -> None:
    """Structural changes (nodes added/removed, workflow renamed) need the full
    envelope PUT; otherwise per-node PUTs of just the touched blocks — the
    predecessor project hit HTTP 413s PUTting large workflows for edge-only
    changes."""
    wf_id = wf.get("id") or wf.get("workflow_id")
    if result["structural"]:
        api.put_workflow(wf_id, wf)
        return
    for node_id in result["touched_node_ids"]:
        block = shapes.block_by_id(wf, node_id)
        if block is not None:
            api.put_node(wf_id, node_id, block)


@mcp.tool()
def list_workflows(search: Optional[str] = None, limit: int = 20, offset: int = 0) -> dict:
    """List the tenant's workflows (id, name, status, timestamps). `search` is
    a substring match on the workflow name. Use to find an existing workflow
    before creating a duplicate of something that already exists.
    """
    raw = api.list_workflows(limit=limit, offset=offset, search=search)
    return raw if isinstance(raw, dict) else {"data": raw}


@mcp.tool()
def get_workflow(workflow_id: str, view: str = "slim", node_id: Optional[str] = None) -> dict:
    """Read a workflow's current state. Call after every mutation you didn't
    make yourself, and before editing a workflow you haven't fetched recently.

    Views:
    - "slim" (default): graph topology + per-node status WITHOUT settings —
      compact enough to keep in context.
    - "node" with node_id: ONE node's full JSON including settings — use to
      read or debug a specific node's configuration.
    - "full": the entire raw envelope. Large; only when truly needed.
    """
    wf = api.get_workflow(workflow_id)
    if view == "full":
        return wf
    if view == "node" or node_id:
        block = shapes.block_by_id(wf, node_id or "")
        if block is None:
            raise ValueError(f"node {node_id} not found in workflow {workflow_id}")
        return block
    return projections.slim_workflow(wf)


@mcp.tool()
def create_workflow(
    name: str, description: str = "", from_play_id: Optional[str] = None
) -> dict:
    """Create a new workflow — empty, or instantiated from a play (template).

    Prefer from_play_id when search_plays found a match for the user's
    objective: the summoned workflow arrives pre-wired and pre-configured;
    then rename it and adapt nodes. Returns the slim view including the new
    workflow_id.
    """
    # New resource — no existing id for the backend to access-gate, so a
    # mid-flight tenant switch would silently create this in the wrong tenant.
    tenant.assert_pinned_active("creating a workflow")
    if from_play_id:
        wf = api.summon_play(from_play_id)
        wf_id = wf.get("id") or wf.get("workflow_id")
        if name and wf_id:
            try:
                api.patch_workflow_no_validation(wf_id, name=name)
                wf["name"] = name
            except Exception:
                pass  # keep the play's name; rename is cosmetic
        return projections.slim_workflow(wf)
    return projections.slim_workflow(api.create_workflow(name, description))


@mcp.tool()
def set_workflow_live(workflow_id: str, live: bool = True, wait: bool = True) -> dict:
    """Publish a workflow live — or take it offline. This is the "go live"
    toggle the web app exposes; once live, the workflow's triggers/listeners
    actually run.

    - live=True  → promote the workflow to live
    - live=False → take it offline

    By default (wait=True) the call blocks until the platform finishes
    promoting and returns the live workflow. Set wait=False to fire-and-forget:
    you get back {request_id, status:"pending"} — poll get_workflow_live_status
    to confirm it went live.

    A workflow must pass validation before it can go live — run
    validate_workflow first if you've been editing it.
    """
    if wait:
        return api.publish_workflow_live(workflow_id, toggle_live=live, async_=False)
    res = api.publish_workflow_live(workflow_id, toggle_live=live, async_=True)
    req_id = res.get("requestId") if isinstance(res, dict) else None
    if req_id:
        return {"request_id": req_id, "status": "pending"}
    return res


@mcp.tool()
def get_workflow_live_status(workflow_id: str, request_id: str) -> dict:
    """Poll the result of a fire-and-forget set_workflow_live(wait=False) call.
    Pass the request_id it returned. Reports {status, ...} and, once finished,
    the live workflow (or an error)."""
    return api.workflow_live_status(workflow_id, request_id)


@mcp.tool()
def edit_workflow(workflow_id: str, operations: list[dict]) -> dict:
    """Apply a batch of graph mutations to a workflow in ONE round trip, then
    validate. Use this for all structural changes (use update_node_settings
    for reconfiguring an existing node's settings).

    Each operation is a dict with an "op" key:

    - {"op": "add_node", "type_id": <node_definition_id from search_nodes>,
       "name": <display name>, "parents": [<node_id or ref>...],
       "settings": {<field_name>: <value>}, "ref": "n1"}
       Optional: description, position {x,y}, is_trigger, is_listener,
       test_mode (default false), labels {field: label}, source_handle,
       force_root, force_demote_listener.
       `parents: []` makes it a start node (refused for action-only types).
       `ref` lets later operations in the same batch reference this node
       before its real id exists.
    - {"op": "add_edge", "source": id|ref, "target": id|ref,
       "source_handle": "_default", "target_handle": "_default"}
    - {"op": "remove_edge", "source": ..., "target": ...}
    - {"op": "remove_node", "node_id": ...}
    - {"op": "rename_node", "node_id": ..., "name": ...}
    - {"op": "rename_workflow", "name": ...}
    - {"op": "set_test_mode", "node_id": ...|"all": true, "value": true}

    Rules enforced (the tool refuses with an explanation): single-input nodes
    take one `_default` edge (joins/merges need a Magic Node, which gets
    df1..df5 handles and auto-maintained references); only one listener per
    workflow; action-only types can't be roots. Settings field names must come
    from get_node_type — see the node-settings skill for shapes and template
    syntax ({{column_name}}, snake_case identifiers).

    Returns operation summary, warnings, ref→id map, and the post-save
    validation report. Fix validation errors before running.
    """
    wf = api.get_workflow(workflow_id)
    result = shapes.apply_operations(wf, operations, _lookup_node_def)
    _persist(wf, result)
    saved = api.get_workflow(workflow_id)
    return {
        "applied": result["summary"],
        "warnings": result["warnings"],
        "node_ids": result["ref_map"],
        "validation": projections.scan_validation(saved),
    }


@mcp.tool()
def update_node_settings(
    workflow_id: str,
    node_id: str,
    settings: dict,
    labels: Optional[dict] = None,
    replace: bool = False,
) -> dict:
    """Set or update an existing node's settings. Merges into current settings
    by default (replace=true wipes them first — rarely what you want).

    `settings` maps field_name → value. Field names MUST come from
    get_node_type / get_field_options output, never guessed. Values for
    dropdown fields must be option `value`s from get_field_options (pass the
    human-readable option label via `labels` so the UI shows it). Group fields
    take a list of {field_name, field_value} dicts as the value; nRev tables
    column fields take a list of lists of those envelopes — shapes documented
    in the node-settings skill.

    Magic Node shortcut: pass `code` (the Python source) and optionally
    `instructions` (a natural-language prompt) and the tool builds the nested
    code_section / instructions_and_ref envelopes the backend requires,
    preserving the auto-maintained input references. Wire the Magic Node's input
    edges (edit_workflow) before setting code so its references exist.

    Returns the node's updated settings plus the workflow validation report.
    """
    wf = api.get_workflow(workflow_id)
    block = shapes.block_by_id(wf, node_id)
    if block is None:
        raise ValueError(f"node {node_id} not found in workflow {workflow_id}")
    if replace:
        block["settings_field_values"] = []
    labels = labels or {}
    # Magic Node code/instructions need bespoke group shaping; everything else is
    # a straight field_name → value upsert.
    for field_name, value in shapes.coerce_magic_settings(block, settings).items():
        shapes.set_setting(block, field_name, value, labels.get(field_name))
    api.put_node(workflow_id, node_id, block)
    saved = api.get_workflow(workflow_id)
    return {
        "node_id": node_id,
        "settings": [
            {"field_name": s.get("field_name"), "field_value": s.get("field_value")}
            for s in shapes.settings_list(shapes.block_by_id(saved, node_id) or block)
        ],
        "validation": projections.scan_validation(saved),
    }


@mcp.tool()
def manage_variables(
    workflow_id: str,
    action: str = "list",
    variable_id: Optional[str] = None,
    variable: Optional[dict] = None,
) -> Any:
    """List/create/update/delete workflow variables — values injected at run
    time (API keys the user supplies per run, a target persona, a date range)
    and referenced from node settings.

    action: "list" (default) | "create" | "update" | "delete".
    `variable` (create/update): {name, data_type, default_value?, ...} where
    data_type ∈ text | multiline_text | date | boolean | file_upload |
    dropdown. `variable_id` required for update/delete.
    """
    if action == "list":
        return api.list_variables(workflow_id)
    if action == "create":
        if not variable:
            raise ValueError("create requires `variable`")
        return api.create_variable(workflow_id, variable)
    if action == "update":
        if not (variable_id and variable):
            raise ValueError("update requires variable_id and `variable`")
        return api.update_variable(workflow_id, variable_id, variable)
    if action == "delete":
        if not variable_id:
            raise ValueError("delete requires variable_id")
        api.delete_variable(workflow_id, variable_id)
        return {"deleted": variable_id}
    raise ValueError(f"unknown action {action!r} — use list | create | update | delete")
