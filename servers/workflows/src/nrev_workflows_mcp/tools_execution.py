"""Execution tools: validate, run, inspect outputs."""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from . import api, projections
from .app import mcp

_DOWNLOAD_ROOT = os.environ.get("NREV_DOWNLOAD_DIR", os.path.expanduser("~/.nrev-mcp/downloads"))
_DOWNLOAD_HARD_CEILING = 1_000_000


@mcp.tool()
def validate_workflow(workflow_id: str) -> dict:
    """Validate a workflow without running it: workflow-level config errors,
    per-node config errors, and Magic Node references that don't point at real
    edges. Call after every edit_workflow / update_node_settings batch and fix
    every error before running — invalid workflows fail late and burn credits.
    """
    return projections.scan_validation(api.get_workflow(workflow_id))


@mcp.tool()
def run_workflow(workflow_id: str, input_data: Optional[dict] = None) -> dict:
    """Execute the whole workflow. `input_data` supplies the manual-trigger
    input form values when the workflow has one.

    Executions consume tenant credits per node per row — while iterating on a
    build, keep nodes in test mode (edit_workflow set_test_mode all=true) so
    inputs are truncated, and prefer run_node for testing a single step.
    Returns the execution_id; follow with get_execution(wait_seconds=...) to
    poll, then get_node_output per node to inspect data.
    """
    raw = api.execute_workflow(workflow_id, input_data)
    exec_id = projections.extract_execution_id(raw)
    return {"execution_id": exec_id, "raw": raw if exec_id is None else None, "status": "started"}


@mcp.tool()
def run_node(workflow_id: str, node_id: str, prior_execution_id: Optional[str] = None) -> dict:
    """Execute a single node — the cheap, fast way to test one step while
    building. With prior_execution_id (from an earlier run), upstream outputs
    are reused from cache and only this node re-runs.

    Returns the execution_id; follow with get_execution then
    get_node_output(workflow_id, execution_id, node_id).
    """
    raw = api.execute_node(workflow_id, node_id, prior_execution_id)
    exec_id = projections.extract_execution_id(raw)
    return {"execution_id": exec_id, "raw": raw if exec_id is None else None, "status": "started"}


@mcp.tool()
def get_execution(
    workflow_id: str,
    execution_id: Optional[str] = None,
    wait_seconds: int = 0,
    poll_interval_seconds: int = 4,
) -> dict:
    """Get an execution's status with per-node statuses and errors. Without
    execution_id, returns the workflow's recent executions list instead.

    `wait_seconds > 0` polls until the execution leaves running state or the
    wait budget is exhausted (use ~60-180 for typical test runs) — one tool
    call instead of a polling loop. A node-level status of completed does NOT
    guarantee the rows succeeded: check get_node_output for row-level `error`
    values on Pipedream and nrev_tables nodes.
    """
    if execution_id is None:
        return api.list_executions(workflow_id)
    deadline = time.monotonic() + max(0, int(wait_seconds))
    while True:
        slim = projections.slim_execution(api.get_execution_detail(workflow_id, execution_id))
        if not slim.get("is_running") or time.monotonic() >= deadline:
            return slim
        time.sleep(max(1, int(poll_interval_seconds)))


@mcp.tool()
def stop_execution(workflow_id: str, execution_id: str) -> dict:
    """Stop a running execution — use when a run is misbehaving or burning
    credits on bad data."""
    return {"result": api.abort_execution(workflow_id, execution_id)}


@mcp.tool()
def get_node_output(
    workflow_id: str,
    execution_id: str,
    node_id: str,
    handle: str = "_default",
    limit: int = 25,
    offset: int = 0,
    search: Optional[str] = None,
    columns: Optional[list[str]] = None,
) -> dict:
    """Inspect a node's output rows from an execution — THE feedback loop for
    judging whether a node actually did the right thing. Always inspect the
    output of new/changed nodes after a test run; check each row's `error`
    field, which is populated even when the node-level status is completed.

    `search` filters rows by substring across all columns server-side (much
    cheaper than paginating). `columns` projects each row to the listed keys —
    use it to skip heavy JSON payload columns. `handle` selects the output
    branch on nodes with success/error or filter splits. Page size caps at 100;
    for whole-dataset analysis use download_node_output instead.
    """
    raw = api.get_node_preview(
        workflow_id, execution_id, node_id,
        handle_condition=handle, skip=offset, limit=limit, search_string=search,
    )
    rows = raw.get("data", []) if isinstance(raw, dict) else (raw or [])
    if columns:
        rows = [{c: r.get(c) for c in columns} for r in rows if isinstance(r, dict)]
    out: dict = {"rows": rows}
    if isinstance(raw, dict) and raw.get("meta"):
        out["meta"] = raw["meta"]
    return out


@mcp.tool()
def download_node_output(
    workflow_id: str,
    execution_id: str,
    node_id: str,
    handle: str = "_default",
    search: Optional[str] = None,
    columns: Optional[list[str]] = None,
    max_rows: int = 100_000,
    target_path: Optional[str] = None,
    overwrite: bool = False,
) -> dict:
    """Download a node's FULL output dataset to a local JSONL file for offline
    analysis (pandas/duckdb/jq) — keeps thousands of rows out of the model
    context. Auto-paginates at the API's 100-row page cap.

    Use when get_node_output's paged window isn't enough: distribution checks,
    group-bys, dedup verification, row-error counts across a big run. Default
    path: ~/.nrev-mcp/downloads/<execution_id>/<node_id>-<handle>.jsonl.
    """
    if max_rows > _DOWNLOAD_HARD_CEILING:
        raise ValueError(f"max_rows exceeds hard ceiling {_DOWNLOAD_HARD_CEILING:_}")
    path = (
        os.path.abspath(os.path.expanduser(target_path))
        if target_path
        else os.path.join(_DOWNLOAD_ROOT, str(execution_id), f"{node_id}-{handle}.jsonl")
    )
    if os.path.exists(path) and not overwrite:
        raise ValueError(f"refusing to overwrite {path!r} — pass overwrite=true or another target_path")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    page, skip, written, total_available = 100, 0, 0, None
    first_keys: list[str] = []
    with open(path, "w", encoding="utf-8") as fh:
        while written < max_rows:
            raw = api.get_node_preview(
                workflow_id, execution_id, node_id,
                handle_condition=handle, skip=skip, limit=page, search_string=search,
            )
            rows = raw.get("data", []) if isinstance(raw, dict) else (raw or [])
            meta = raw.get("meta") or {} if isinstance(raw, dict) else {}
            if total_available is None:
                total_available = meta.get("total_entries")
            if not rows:
                break
            if columns:
                rows = [{c: r.get(c) for c in columns} for r in rows if isinstance(r, dict)]
            for row in rows:
                if not first_keys and isinstance(row, dict):
                    first_keys = list(row.keys())
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                written += 1
                if written >= max_rows:
                    break
            if len(rows) < page:
                break
            skip += page

    return {
        "path": path,
        "rows_downloaded": written,
        "rows_available": total_available,
        "complete": total_available is None or written >= (total_available or 0),
        "columns": first_keys,
        "hint": f"pandas: pd.read_json({path!r}, lines=True)",
    }
