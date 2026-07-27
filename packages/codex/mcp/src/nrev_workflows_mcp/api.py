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

from typing import Any, Optional

from . import config
from .transport import request as _request


def request(
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    files: Optional[dict] = None,
) -> Any:
    return _request(config.workflow_host(), method, path, json_body=json_body, params=params, files=files)


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
    # `name` is REQUIRED by the backend's DuplicateWorkflowRequest — callers
    # without one should derive it from the source workflow's name first.
    body = {"name": new_name} if new_name else {}
    return request("POST", f"/workflows/{wf_id}/duplicate", json_body=body)


def download_workflow_json(wf_id: str) -> Any:
    """GET /workflows/{id}/download-json — the full workflow export the web
    app's Download button streams: the workflow envelope plus its `variables`
    and the `nrev_tables` schema expressions it references. Path verified
    against the FE WorkflowApiService.downloadWorkflowJson (GET, JSON body)."""
    return request("GET", f"/workflows/{wf_id}/download-json")


def upload_workflow_json(filename: str, content: bytes) -> dict:
    """POST /workflows/upload-json — import a previously exported workflow.
    Multipart upload, field name `upload_file`, content type must be
    application/json (the backend rejects anything else). Verified against the
    FE WorkflowApiService.uploadWorkflowJson + the backend route."""
    return request(
        "POST",
        "/workflows/upload-json",
        files={"upload_file": (filename, content, "application/json")},
    )


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


def generate_connection_url(
    connection_app_id: str, success_redirect_uri: str, error_redirect_uri: str
) -> dict:
    """POST /connections/{app}/url — mint the hosted OAuth/connect URL for an
    app. Both redirect URIs are REQUIRED by the backend's ConnectionUrlRequest
    (camelCase aliases). Response: {connectUrl, expiresIn, appId}. Verified
    against the backend connection_endpoints.generate_connection_url."""
    return request(
        "POST",
        f"/connections/{connection_app_id}/url",
        json_body={
            "successRedirectUri": success_redirect_uri,
            "errorRedirectUri": error_redirect_uri,
        },
    )


# ── Listeners (webhook/trigger test lifecycle) ───────────────────────────────
# Paths + param names verified against the FE ListenersApiService.


def activate_listener_test(wf_id: str, node_id: str, execution_mode: str = "semi_workflow") -> Any:
    """POST /listeners/workflow/{wf}/node/{n}/activate-test?execution_mode=…
    Arms a listener node to capture its next incoming event for testing.
    execution_mode: semi_workflow (capture only) | full_workflow (run the
    workflow off the captured event)."""
    return request(
        "POST",
        f"/listeners/workflow/{wf_id}/node/{node_id}/activate-test",
        params={"execution_mode": execution_mode},
    )


def get_listener_latest_event(wf_id: str, node_id: str, historical: bool = False) -> dict:
    """GET /listeners/workflow/{wf}/node/{n}/latest-event?historical=…
    Polls for the armed listener's event. historical=true returns the latest
    event up to now; false only events after the listener last listened.
    Response: {status: running|completed|failed|timeout, data: [...], error}."""
    return request(
        "GET",
        f"/listeners/workflow/{wf_id}/node/{node_id}/latest-event",
        params={"historical": "true" if historical else "false"},
    )


def deactivate_listener(wf_id: str, node_id: str) -> Any:
    """POST /listeners/workflow/{wf}/node/{n}/deactivate — disarm the listener."""
    return request("POST", f"/listeners/workflow/{wf_id}/node/{node_id}/deactivate", json_body={})


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


def list_global_executions(limit: int = 20, skip: int = 0, search: Optional[str] = None) -> dict:
    """GET /execution-logs — run history across ALL workflows in the tenant
    (the web app's Run Logs page). Returns {data: [...], meta} where each item
    carries id/status/startedAt/creditsUsed/workflowName/workflowId. Params
    verified against the FE WorkflowApiService.getGlobalRunLogs."""
    params: dict = {"skip": max(0, int(skip)), "limit": int(limit)}
    if search:
        params["search"] = search
    return request("GET", "/execution-logs", params=params)


def execution_stats(date_range: Optional[str] = None) -> dict:
    """GET /execution-logs/stats — tenant-wide execution stats: credits
    consumed + total executions, each with a total, percent change, and a time
    series. `date_range`: last_day | last_week | last_month (default) |
    last_3_months (backend enum; passed as `dateRange`). Verified against the
    FE RunLogsApiService.useGetRunLogsStats + the backend route."""
    params = {"dateRange": date_range} if date_range else None
    return request("GET", "/execution-logs/stats", params=params)


def get_execution_detail(wf_id: str, exec_id: str, only_latest: bool = True) -> dict:
    """GET the run log for one execution. The response carries the per-node-RUN
    list under `blockRuns` (one entry per block execution). `only_latest=false`
    returns ALL runs (a node in a loop/fan-out appears once per run);
    `only_latest=true` collapses to the latest run per node."""
    return request(
        "GET",
        f"/execution-logs/workflow/{wf_id}/workflow-execution/{exec_id}",
        params={"only_latest": "true" if only_latest else "false"},
    )


def stop_execution(wf_id: str, exec_id: str) -> Any:
    """POST .../workflow-execution/{id}/stop — what the UI's stop button calls
    (verified against the FE WorkflowApiService.stopWorkflowExecution; replaces
    the predecessor's UNVERIFIED `/abort` path, which 404s). Returns 204/empty
    on success."""
    return request("POST", f"/executions/workflow/{wf_id}/workflow-execution/{exec_id}/stop")


def stop_node_execution(wf_id: str, exec_id: str, node_execution_id: str) -> Any:
    """POST .../node-execution/{id}/stop — stop ONE node run inside an
    execution (the per-node stop button). Verified against the FE
    WorkflowApiService.stopNodeExecution. Returns 204/empty on success."""
    return request(
        "POST",
        f"/executions/workflow/{wf_id}/workflow-execution/{exec_id}/node-execution/{node_execution_id}/stop",
    )


def resume_execution(wf_id: str, exec_id: str, envelope: Optional[dict] = None) -> dict:
    """POST /workflows/{wf}/execution/{exec}/update-and-resume — resume a
    paused/stopped execution, optionally saving an updated workflow envelope
    first (the backend takes `workflow_details` as an OPTIONAL embedded body —
    omit it to resume as-is). Returns {workflow, execution}. Verified against
    the FE WorkflowApiService.resumeWorkflowExecution + the backend route."""
    body = {"workflow_details": envelope} if envelope else {}
    return request("POST", f"/workflows/{wf_id}/execution/{exec_id}/update-and-resume", json_body=body)


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


def get_node_execution_preview(
    wf_id: str,
    exec_id: str,
    node_execution_id: str,
    handle_condition: str = "_default",
    skip: int = 0,
    limit: int = 50,
    search_string: Optional[str] = None,
) -> dict:
    """Preview the output of ONE specific node run, addressed by its
    node_execution_id (from the execution's `blockRuns`). The by-node preview
    above only returns a node's LATEST run; this targets any run — needed when
    a node executed many times (loops/fan-out). Same Pagination shape."""
    limit = max(1, min(int(limit), 100))  # >100 silently returns 0 rows
    params: dict = {"handle_condition": handle_condition, "skip": max(0, int(skip)), "limit": limit}
    if search_string:
        params["search_string"] = search_string
    return request(
        "GET",
        f"/executions/workflow/{wf_id}/workflow-execution/{exec_id}/node-execution/{node_execution_id}/preview",
        params=params,
    )


# ── Plays (workflow templates) ───────────────────────────────────────────────


def list_playbooks(
    search: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    categories: Optional[str] = None,
) -> dict:
    """GET /playbooks — the published play catalog, grouped into playbooks.

    Each item in the returned `data` list is a playbook carrying a nested
    `plays` array (each play has playId/name/description/categories), plus a
    `meta` block with pagination. This is the endpoint the web app uses for
    play search/listing.

    Replaces the former GET /plays/multi, which 500s server-side for every
    input (Postgres 42P10: "for SELECT DISTINCT, ORDER BY expressions must
    appear in select list"). Verified against production 2026-06-11.

    `categories` is an optional comma-separated list of category ids — see
    GET /play-categories for the id↔name mapping (e.g. 3 = Sales)."""
    params: dict = {"skip": int(offset), "limit": int(limit)}
    if search:
        params["search"] = search
    if categories:
        params["categories"] = categories
    return request("GET", "/playbooks", params=params)


def summon_play(play_id: str) -> dict:
    """POST /plays/{id}/summon — creates a new workflow from the play and
    returns the workflow object. UNVERIFIED body (route takes no body fields
    beyond auth-derived user)."""
    return request("POST", f"/plays/{play_id}/summon", json_body={})


# ── Live publish (the "go live" toggle) ──────────────────────────────────────


def publish_workflow_live(workflow_id: str, toggle_live: bool = True, async_: bool = False) -> dict:
    """POST /live/workflow/{workflow_id}/publish — promote a workflow to live
    (toggle_live=True) or take it offline (toggle_live=False).

    Synchronous (async_=False, default): blocks until promotion finishes and
    returns {workflow, gracePeriodSeconds}. Asynchronous (async_=True): returns
    {requestId} immediately — poll workflow_live_status() for completion.
    Schema verified against the production OpenAPI contract 2026-06-11."""
    params = {"async": "true"} if async_ else None
    return request(
        "POST",
        f"/live/workflow/{workflow_id}/publish",
        json_body={"toggleLive": bool(toggle_live)},
        params=params,
    )


def workflow_live_status(workflow_id: str, request_id: str) -> dict:
    """GET /live/workflow/{workflow_id}/publish/status — poll an async live
    publish started with publish_workflow_live(async_=True). Returns
    {status, requestId, workflow?, gracePeriodSeconds?, error?}."""
    return request(
        "GET",
        f"/live/workflow/{workflow_id}/publish/status",
        params={"requestId": request_id},
    )


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
