"""Execution tools: validate, run, inspect outputs."""
from __future__ import annotations

import json
import os
import re
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


def _live_nodes(wf: dict) -> list[dict]:
    """Blocks NOT in test mode — the ones that spend real credits on a run."""
    return [b for b in (wf.get("blocks") or []) if not projections._pick(b, "isTestMode", "is_test_mode", default=False)]


@mcp.tool()
def estimate_run_cost(workflow_id: str, rows: int = 100) -> dict:
    """Estimate the credit cost of a full (non-test) run BEFORE launching one.
    Sums each node's per-row credit cost over an assumed `rows` input. The
    figure is an upper bound — filters, dedup, and qualification splits shrink
    the row count downstream, so actual spend is lower. Returns the total,
    a per-node breakdown, and the top cost drivers. Show this to the user before
    a full run.
    """
    wf = api.get_workflow(workflow_id)
    return projections.estimate_cost(wf.get("blocks") or [], rows)


@mcp.tool()
def run_workflow(
    workflow_id: str, input_data: Optional[dict] = None, confirm: bool = False, est_rows: int = 100
) -> dict:
    """Execute the whole workflow. `input_data` supplies the manual-trigger
    input form values when the workflow has one.

    Executions consume tenant credits per node per row — while iterating on a
    build, keep nodes in test mode (edit_workflow set_test_mode all=true) so
    inputs are truncated, and prefer run_node for testing a single step.

    SPEND GATE: if any node is live (not in test mode) this is a real-credit run
    and is REFUSED unless `confirm=true`. When refused it returns the cost
    estimate and the list of live nodes instead of running — show the user the
    estimated credits and get their go-ahead, then call again with confirm=true.
    A fully test-mode workflow runs without confirmation (inputs are truncated,
    spend is negligible). `est_rows` feeds the estimate shown on refusal.

    Returns the execution_id; follow with get_execution(wait_seconds=...) to
    poll, then get_node_output per node to inspect data.
    """
    if not confirm:
        wf = api.get_workflow(workflow_id)
        live = _live_nodes(wf)
        if live:
            return {
                "status": "blocked",
                "reason": (
                    f"{len(live)} node(s) are live (not in test mode) — a full run spends real "
                    f"credits. Review the estimate, then re-call run_workflow with confirm=true. "
                    f"To iterate cheaply instead, set_test_mode all=true and use run_node."
                ),
                "estimate": projections.estimate_cost(wf.get("blocks") or [], est_rows),
                "live_nodes": [{"node_id": b.get("id"), "name": projections._pick(b, "variableName", "variable_name")} for b in live],
            }
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
    only_latest: bool = False,
) -> dict:
    """Get an execution's status plus the per-node-RUN breakdown. Without
    execution_id, returns the workflow's recent executions list instead.

    `node_runs` is one entry PER block execution — a node that runs many times
    (loops, fan-out, per-batch) appears once per run, each with its own
    `node_execution_id`, status, duration, credits_used, and row_count.
    `node_execution_count` is the total blocks executed (e.g. "131 blocks").
    To read the rows of a SPECIFIC run, pass its `node_execution_id` to
    get_node_output; passing only node_id there returns just the latest run.

    `only_latest=true` collapses `node_runs` to the latest run per node (the
    default false returns every run). `wait_seconds > 0` polls until the
    execution leaves running state or the wait budget is exhausted (use
    ~60-180 for typical test runs) — one tool call instead of a polling loop.
    A node-level status of completed does NOT guarantee the rows succeeded:
    check get_node_output for row-level `error` values on Pipedream and
    nrev_tables nodes.
    """
    if execution_id is None:
        return api.list_executions(workflow_id)
    deadline = time.monotonic() + max(0, int(wait_seconds))
    while True:
        slim = projections.slim_execution(
            api.get_execution_detail(workflow_id, execution_id, only_latest=only_latest)
        )
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
    node_id: Optional[str] = None,
    handle: str = "_default",
    limit: int = 25,
    offset: int = 0,
    search: Optional[str] = None,
    columns: Optional[list[str]] = None,
    node_execution_id: Optional[str] = None,
) -> dict:
    """Inspect a node's output rows from an execution — THE feedback loop for
    judging whether a node actually did the right thing. Always inspect the
    output of new/changed nodes after a test run; check each row's `error`
    field, which is populated even when the node-level status is completed.

    Selecting WHICH run to read (both addressed under execution_id):
    - `node_id` → the node's LATEST run in this execution.
    - `node_execution_id` → ONE specific run. Get it from get_execution's
      `node_runs`; this is the only way to read a non-latest run of a node
      that executed multiple times (loops/fan-out). Takes precedence over
      node_id when both are given.
    Pass exactly one. Do NOT substitute a different execution id here to reach
    another run — that addresses a workflow_executions resource and 403s; use
    node_execution_id under the run's own execution_id instead.

    `search` filters rows by substring across all columns server-side (much
    cheaper than paginating). `columns` projects each row to the listed keys —
    use it to skip heavy JSON payload columns. `handle` selects the output
    branch on nodes with success/error or filter splits. Page size caps at 100;
    for whole-dataset analysis use download_node_output instead.
    """
    if not node_execution_id and not node_id:
        raise ValueError(
            "pass node_id (the node's latest run) or node_execution_id "
            "(a specific run from get_execution's node_runs)"
        )
    if node_execution_id:
        raw = api.get_node_execution_preview(
            workflow_id, execution_id, node_execution_id,
            handle_condition=handle, skip=offset, limit=limit, search_string=search,
        )
    else:
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
    node_id: Optional[str] = None,
    handle: str = "_default",
    search: Optional[str] = None,
    columns: Optional[list[str]] = None,
    max_rows: int = 100_000,
    target_path: Optional[str] = None,
    overwrite: bool = False,
    node_execution_id: Optional[str] = None,
) -> dict:
    """Download a node's FULL output dataset to a local JSONL file for offline
    analysis (pandas/duckdb/jq) — keeps thousands of rows out of the model
    context. Auto-paginates at the API's 100-row page cap.

    Like get_node_output: `node_id` downloads the node's LATEST run, while
    `node_execution_id` (from get_execution's `node_runs`) downloads ONE
    specific run — the only way to get a non-latest run of a multi-run node.
    Pass exactly one (node_execution_id wins if both are set).

    Use when get_node_output's paged window isn't enough: distribution checks,
    group-bys, dedup verification, row-error counts across a big run. Default
    path: ~/.nrev-mcp/downloads/<execution_id>/<node_id>-<handle>.jsonl.
    """
    if max_rows > _DOWNLOAD_HARD_CEILING:
        raise ValueError(f"max_rows exceeds hard ceiling {_DOWNLOAD_HARD_CEILING:_}")
    if not node_execution_id and not node_id:
        raise ValueError(
            "pass node_id (the node's latest run) or node_execution_id "
            "(a specific run from get_execution's node_runs)"
        )
    file_stem = node_execution_id or node_id
    path = (
        os.path.abspath(os.path.expanduser(target_path))
        if target_path
        else os.path.join(_DOWNLOAD_ROOT, str(execution_id), f"{file_stem}-{handle}.jsonl")
    )
    if os.path.exists(path) and not overwrite:
        raise ValueError(f"refusing to overwrite {path!r} — pass overwrite=true or another target_path")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    page, skip, written, total_available = 100, 0, 0, None
    first_keys: list[str] = []
    with open(path, "w", encoding="utf-8") as fh:
        while written < max_rows:
            if node_execution_id:
                raw = api.get_node_execution_preview(
                    workflow_id, execution_id, node_execution_id,
                    handle_condition=handle, skip=skip, limit=page, search_string=search,
                )
            else:
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


_ERROR_COL_RE = re.compile(r"^error(_\d+)?$", re.IGNORECASE)


def _row_error(row: dict):
    """Return a row's row-level error value if any `error` / `error_N` column is
    populated, else None. Empty string / null / empty container = no error."""
    for key, val in row.items():
        if _ERROR_COL_RE.match(str(key)) and val not in (None, "", {}, [], "null"):
            return val
    return None


@mcp.tool()
def check_node_errors(
    workflow_id: str,
    execution_id: str,
    node_id: Optional[str] = None,
    node_execution_id: Optional[str] = None,
    handle: str = "_default",
    max_rows: int = 200,
    sample: int = 5,
) -> dict:
    """Scan a node's OUTPUT ROWS for row-level errors that the node-level
    `completed` status hides. Pipedream-wrapped actions (Slack/Gmail/Sheets/…)
    and nrev_tables nodes (Add Row / Update Row) report status=completed even
    when individual rows failed — the real error sits in the row's `error`
    column. get_execution shows node-level status; this confirms the rows
    themselves actually succeeded. Run it after writes to catch silent failures.

    Target the node like get_node_output: `node_execution_id` (a specific run
    from get_execution's node_runs) takes precedence, else `node_id` (the
    node's latest run). Scans up to `max_rows` rows on `handle` and returns the
    error count plus up to `sample` example {row_index, error} entries.
    """
    if not node_execution_id and not node_id:
        raise ValueError(
            "pass node_id (the node's latest run) or node_execution_id "
            "(a specific run from get_execution's node_runs)"
        )
    scanned, errors = 0, []
    skip, page, stop = 0, 100, False
    while scanned < max_rows and not stop:
        if node_execution_id:
            raw = api.get_node_execution_preview(
                workflow_id, execution_id, node_execution_id,
                handle_condition=handle, skip=skip, limit=page,
            )
        else:
            raw = api.get_node_preview(
                workflow_id, execution_id, node_id,
                handle_condition=handle, skip=skip, limit=page,
            )
        rows = raw.get("data", []) if isinstance(raw, dict) else (raw or [])
        if not rows:
            break
        for i, row in enumerate(rows):
            scanned += 1
            if isinstance(row, dict):
                err = _row_error(row)
                if err is not None:
                    errors.append({"row_index": skip + i, "error": err})
            if scanned >= max_rows:
                stop = True
                break
        if len(rows) < page:
            break
        skip += page
    return {
        "rows_scanned": scanned,
        "rows_with_errors": len(errors),
        "clean": len(errors) == 0,
        "errors": errors[: max(0, sample)],
    }
