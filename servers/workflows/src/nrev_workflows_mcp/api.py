"""Thin wrappers over the nRev workflow platform REST API.

Endpoint paths, body-wrapping shapes, and parameter quirks here were verified
against production by the predecessor project (nrev-workflow-mcp v0.2.x) —
don't "simplify" a wrapper without re-verifying against the live API:

  - PUT /workflows/{id}            body wrapped in {"workflow_details": {...}}
  - POST /workflows                body wrapped in {"workflow_details": {...}}
  - PUT /workflows/{wf}/nodes/{id} body wrapped in {"node": {...}}
  - update-and-execute             body wrapped in {"workflow": {...}}
  - GET /workflows list filter param is `name` (substring), not `search`
  - /node_definitions limit is capped at 100 (422 above)
  - node preview limit is capped at 100 (silently returns 0 rows above)

Paths marked UNVERIFIED were taken from the workflow_studio FastAPI route
definitions but have not yet been exercised against production by this server.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from .transport import request as _request

HOST = os.environ.get("NREV_WF_HOST", "https://workflow.public.prod.nurturev.com")


def request(method: str, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
    return _request(HOST, method, path, json_body=json_body, params=params)


# ── Workflows ────────────────────────────────────────────────────────────────


def get_workflow(wf_id: str) -> dict:
    return request("GET", f"/workflows/{wf_id}")


def list_workflows(limit: int = 20, offset: int = 0, search: Optional[str] = None) -> dict:
    params: dict = {"limit": int(limit), "skip": int(offset)}
    if search:
        params["name"] = search  # platform's filter param is `name`, not `search`
    return request("GET", "/workflows", params=params)


def create_workflow(name: str, description: str = "") -> dict:
    return request(
        "POST",
        "/workflows",
        json_body={"workflow_details": {"name": name, "description": description, "blocks": []}},
    )


def put_workflow(wf_id: str, envelope: dict) -> dict:
    return request("PUT", f"/workflows/{wf_id}", json_body={"workflow_details": envelope})


def put_node(wf_id: str, node_id: str, node: dict) -> dict:
    return request("PUT", f"/workflows/{wf_id}/nodes/{node_id}", json_body={"node": node})


def patch_workflow_no_validation(
    wf_id: str, *, name: Optional[str] = None, sticky_notes: Optional[list[dict]] = None
) -> dict:
    body: dict = {}
    if name is not None:
        body["name"] = name
    if sticky_notes is not None:
        body["stickyNotes"] = sticky_notes  # camelCase despite the published schema
    if not body:
        raise ValueError("must pass at least one of name / sticky_notes")
    return request("PATCH", f"/workflows/{wf_id}/no-validation", json_body=body)


def duplicate_workflow(wf_id: str, new_name: Optional[str] = None) -> dict:
    body = {"name": new_name} if new_name else {}
    return request("POST", f"/workflows/{wf_id}/duplicate", json_body=body)


# ── Node definitions catalog ─────────────────────────────────────────────────


def list_node_definitions(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    category: Optional[str] = None,
    only_trigger: bool = False,
    only_action: bool = False,
) -> dict:
    params: dict = {"limit": max(1, min(int(limit), 100)), "skip": max(0, int(offset))}
    if search:
        params["search"] = search
    if category:
        params["category"] = category
    if only_trigger:
        params["onlyTrigger"] = "true"
    if only_action:
        params["onlyAction"] = "true"
    return request("GET", "/node_definitions", params=params)


def get_node_definition(node_definition_id: str) -> dict:
    return request("GET", f"/node_definitions/{node_definition_id}")


def list_node_definition_categories(limit: int = 100) -> dict:
    return request("GET", "/node_definitions/categories", params={"limit": int(limit)})


# ── Connections ──────────────────────────────────────────────────────────────


def list_connections(connection_app_id: Optional[str] = None) -> list:
    """Unfiltered: only the JWT user's own connections. Filtered by app id:
    ALL connections in the tenant for that app (what the UI's picker uses)."""
    params: dict = {}
    if connection_app_id:
        params["connectionAppId"] = connection_app_id
    return request("GET", "/connections", params=params)


def list_connection_apps(
    limit: int = 50, offset: int = 0, category: Optional[str] = None, search: Optional[str] = None
) -> dict:
    params: dict = {"limit": int(limit), "skip": int(offset)}
    if category:
        params["category"] = category
    if search:
        params["search"] = search
    return request("GET", "/connections/apps", params=params)


# ── Node field schema / options ──────────────────────────────────────────────


def field_options(
    node_id: str,
    node_definition_id: str,
    field_name: str,
    settings: list[dict],
    search: Optional[str] = None,
) -> dict:
    """POST /nodes/field-options — dropdown options for one field.

    `node_id` is for logging only — any UUID works; it doesn't have to exist.
    For cascading dropdowns include prerequisite settings in `settings`.
    """
    body = {
        "nodeId": node_id,
        "nodeDefinitionId": node_definition_id,
        "fieldName": field_name,
        "settings": settings or [],
    }
    if search is not None:
        body["search"] = search
    return request("POST", "/nodes/field-options", json_body=body)


def updated_node_config(
    node_id: str,
    node_definition_id: str,
    field_name_changed: str,
    setting_field_values: list[dict],
    settings_schema: Optional[list[dict]] = None,
) -> dict:
    """POST /nodes/updated-config-and-status — materialize an action's full
    field schema given current settings. Works cross-tenant (unlike
    reload-props, which 400s on teammates' connections)."""
    body = {
        "nodeId": node_id,
        "nodeDefinitionId": node_definition_id,
        "fieldNameChanged": field_name_changed,
        "settingFieldValues": setting_field_values,
        "settingsSchema": settings_schema or [],
    }
    return request("POST", "/nodes/updated-config-and-status", json_body=body)


def reload_pipedream_props(
    node_id: str, node_definition_id: str, field_name_changed: str, settings: list[dict]
) -> dict:
    """POST /nodes/reload-props — Pipedream DYNAMIC fields (col_NNNN per sheet
    column, dynamic_props_id, array fields). NOT idempotent — each call issues
    a fresh dynamic_props_id; call once per real settings change. Body uses
    `settings` (plain {field_name, field_value} pairs), not settingFieldValues.
    """
    body = {
        "nodeId": node_id,
        "nodeDefinitionId": node_definition_id,
        "fieldNameChanged": field_name_changed,
        "settings": settings,
    }
    return request("POST", "/nodes/reload-props", json_body=body)


# ── Execution ────────────────────────────────────────────────────────────────


def execute_workflow(wf_id: str, input_data: Optional[dict] = None) -> Any:
    """POST /executions/workflow/{wf}/execute — run the whole workflow.

    UNVERIFIED body key: the platform's manual-trigger input form posts initial
    input data; if a run with input_data 422s, capture the exact body from the
    web app's network tab and adjust here.
    """
    body: dict = {}
    if input_data:
        body["initialInputData"] = input_data
    return request("POST", f"/executions/workflow/{wf_id}/execute", json_body=body)


def update_workflow_and_execute(wf_id: str, node_id: str, envelope: dict) -> dict:
    """Atomic save-then-execute (what the UI's Run button calls). Avoids the
    stale-state bugs seen with separate PUT + execute calls."""
    return request(
        "POST",
        f"/workflows/{wf_id}/nodes/{node_id}/update-workflow-and-execute",
        json_body={"workflow": envelope},
    )


def execute_node(wf_id: str, node_id: str, prior_execution_id: Optional[str] = None) -> Any:
    """Execute a single node; with prior_execution_id it reuses cached upstream
    output and re-runs from this node forward."""
    body: dict = {}
    if prior_execution_id:
        body["workflowExecutionId"] = prior_execution_id
    return request("POST", f"/executions/workflow/{wf_id}/node/{node_id}/execute", json_body=body)


def list_executions(wf_id: str, limit: int = 10) -> dict:
    return request("GET", f"/execution-logs/workflow/{wf_id}", params={"limit": limit})


def get_execution_detail(wf_id: str, exec_id: str) -> dict:
    return request("GET", f"/execution-logs/workflow/{wf_id}/workflow-execution/{exec_id}")


def abort_execution(wf_id: str, exec_id: str) -> Any:
    """UNVERIFIED path (predecessor flagged it may 404 — capture from the UI's
    stop button if so)."""
    return request("POST", f"/executions/workflow/{wf_id}/workflow-execution/{exec_id}/abort")


def get_node_preview(
    wf_id: str,
    exec_id: str,
    node_id: str,
    handle_condition: str = "_default",
    skip: int = 0,
    limit: int = 50,
    search_string: Optional[str] = None,
) -> dict:
    limit = max(1, min(int(limit), 100))  # >100 silently returns 0 rows
    params: dict = {"handle_condition": handle_condition, "skip": max(0, int(skip)), "limit": limit}
    if search_string:
        params["search_string"] = search_string
    return request(
        "GET",
        f"/executions/workflow/{wf_id}/workflow-execution/{exec_id}/node/{node_id}/preview",
        params=params,
    )


# ── Plays (workflow templates) ───────────────────────────────────────────────


def list_plays(search: Optional[str] = None, limit: int = 10, offset: int = 0) -> dict:
    """GET /plays/multi — UNVERIFIED param names (taken from workflow_studio
    route signature: search + pagination)."""
    params: dict = {"limit": int(limit), "skip": int(offset)}
    if search:
        params["search"] = search
    return request("GET", "/plays/multi", params=params)


def summon_play(play_id: str) -> dict:
    """POST /plays/{id}/summon — creates a new workflow from the play and
    returns the workflow object. UNVERIFIED body (route takes no body fields
    beyond auth-derived user)."""
    return request("POST", f"/plays/{play_id}/summon", json_body={})


# ── Workflow variables ───────────────────────────────────────────────────────
# Router prefix verified in workflow_studio: /workflow/{workflow_id}/variables


def list_variables(wf_id: str) -> Any:
    return request("GET", f"/workflow/{wf_id}/variables")


def create_variable(wf_id: str, payload: dict) -> Any:
    """UNVERIFIED body — common fields: name, data_type (text | multiline_text |
    date | boolean | file_upload | dropdown), default_value. Capture the exact
    shape from the web app if a 422 comes back."""
    return request("POST", f"/workflow/{wf_id}/variables", json_body=payload)


def update_variable(wf_id: str, variable_id: str, payload: dict) -> Any:
    return request("PUT", f"/workflow/{wf_id}/variables/{variable_id}", json_body=payload)


def delete_variable(wf_id: str, variable_id: str) -> Any:
    return request("DELETE", f"/workflow/{wf_id}/variables/{variable_id}")


# ── Credits ──────────────────────────────────────────────────────────────────


def credit_balance() -> Any:
    # The tenant_id path segment is ignored by the server (resolved from JWT).
    return request("GET", "/credit-management/tenant/0/balance")
