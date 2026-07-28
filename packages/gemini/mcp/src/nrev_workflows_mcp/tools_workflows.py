"""Workflow CRUD and mutation tools."""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from . import api, projections, shapes, tenant
from .app import mcp

_EXPORT_ROOT = os.environ.get("NREV_DOWNLOAD_DIR", os.path.expanduser("~/.nrev-mcp/downloads"))

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
def list_workflows(
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    tag_ids: Optional[list[str]] = None,
) -> dict:
    """List the tenant's workflows (id, name, status, timestamps, tag_ids).
    `search` is a substring match on the workflow name. `tag_ids` filters to
    workflows carrying EVERY listed tag id (AND) — get ids from list_tags.
    Use to find an existing workflow before creating a duplicate of something
    that already exists.
    """
    raw = api.list_workflows(limit=limit, offset=offset, search=search, tag_ids=tag_ids)
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
def set_workflow_tags(workflow_id: str, tag_ids: list[str]) -> dict:
    """Set a workflow's tags to EXACTLY this list (converge, not additive) —
    `[]` detaches every tag. Get tag ids from list_tags / find_or_create_tag.
    For add/remove-one semantics use add_workflow_tag / remove_workflow_tag
    instead, which read the current set for you."""
    return projections.slim_workflow(api.set_workflow_tags(workflow_id, tag_ids))


@mcp.tool()
def add_workflow_tag(workflow_id: str, tag_id: str) -> dict:
    """Attach one tag to a workflow without disturbing its other tags (reads
    the current tag_ids, adds this one, writes the full set back — the
    backend itself has no additive primitive)."""
    current = set(projections.slim_workflow(api.get_workflow(workflow_id)).get("tag_ids") or [])
    current.add(tag_id)
    return projections.slim_workflow(api.set_workflow_tags(workflow_id, sorted(current)))


@mcp.tool()
def remove_workflow_tag(workflow_id: str, tag_id: str) -> dict:
    """Detach one tag from a workflow without disturbing its other tags
    (reads the current tag_ids, drops this one, writes the full set back)."""
    current = set(projections.slim_workflow(api.get_workflow(workflow_id)).get("tag_ids") or [])
    current.discard(tag_id)
    return projections.slim_workflow(api.set_workflow_tags(workflow_id, sorted(current)))


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


@mcp.tool()
def duplicate_workflow(workflow_id: str, new_name: Optional[str] = None) -> dict:
    """Clone an existing workflow (draft version) — the fast way to iterate on
    a variant without touching the original, or to reuse a proven build for a
    new segment. Without `new_name` the copy is named "<original> (copy)".
    Returns the slim view of the new workflow including its workflow_id."""
    if not new_name:
        # The platform requires a name on duplicate; derive one from the source.
        source = api.get_workflow(workflow_id)
        new_name = f"{source.get('name') or 'Workflow'} (copy)"
    return projections.slim_workflow(api.duplicate_workflow(workflow_id, new_name))


@mcp.tool()
def export_workflow(workflow_id: str, target_path: Optional[str] = None, overwrite: bool = False) -> dict:
    """Export a workflow's full JSON (the envelope plus its variables and the
    schema of every nRev table it references) to a local file — for backup,
    migration between tenants/environments (re-import with import_workflow),
    or offline inspection. The payload is large, so it is written to disk
    rather than returned inline. Default path:
    ~/.nrev-mcp/downloads/workflows/<workflow_id>.json."""
    raw = api.download_workflow_json(workflow_id)
    path = (
        os.path.abspath(os.path.expanduser(target_path))
        if target_path
        else os.path.join(_EXPORT_ROOT, "workflows", f"{workflow_id}.json")
    )
    if os.path.exists(path) and not overwrite:
        raise ValueError(f"refusing to overwrite {path!r} — pass overwrite=true or another target_path")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    summary: dict = {"path": path, "bytes": len(text.encode("utf-8"))}
    if isinstance(raw, dict):
        summary["workflow_name"] = raw.get("name")
        summary["node_count"] = len(raw.get("blocks") or [])
        summary["variable_count"] = len(raw.get("variables") or [])
    return summary


@mcp.tool()
def import_workflow(file_path: str) -> dict:
    """Create a NEW workflow from an export_workflow JSON file (the platform's
    upload-json import) — restores the graph, settings, and variables into the
    ACTIVE tenant. Connections and table references may need re-pointing after
    import (run validate_workflow and fix what it flags). Returns the slim view
    of the created workflow."""
    # New resource — no existing id for the backend to access-gate, so a
    # mid-flight tenant switch would silently create this in the wrong tenant.
    tenant.assert_pinned_active("importing a workflow")
    path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.isfile(path):
        raise ValueError(f"no file at {path!r}")
    with open(path, "rb") as fh:
        content = fh.read()
    try:
        json.loads(content)
    except Exception as exc:
        raise ValueError(f"{path!r} is not valid JSON (the backend only accepts application/json): {exc}")
    return projections.slim_workflow(api.upload_workflow_json(os.path.basename(path), content))
