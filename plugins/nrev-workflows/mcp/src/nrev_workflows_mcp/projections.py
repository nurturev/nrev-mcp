"""Compact projections of platform responses.

Raw platform payloads are large (a single node's settings schema can be
thousands of tokens). Every read tool projects to the smallest view that still
lets the agent act, with `view="full"` escapes where raw data matters.
Projections are tolerant of camelCase/snake_case drift between API versions.
"""
from __future__ import annotations

from typing import Any, Optional

from . import shapes


def _pick(d: dict, *names: str, default: Any = None) -> Any:
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return default


# ── Workflows ────────────────────────────────────────────────────────────────


def slim_block(block: dict) -> dict:
    return {
        "id": block.get("id"),
        "name": _pick(block, "variableName", "variable_name"),
        "type_id": _pick(block, "typeId", "node_definition_id"),
        "type_slug": block.get("value"),
        "is_trigger": _pick(block, "isTrigger", "is_trigger", default=False),
        "is_listener": _pick(block, "isListener", "is_listener", default=False),
        "is_test_mode": _pick(block, "isTestMode", "is_test_mode", default=False),
        "is_orphan": _pick(block, "isOrphan", "is_orphan", default=False),
        "config_error": block.get("node_config_error"),
        "credit_cost_per_item": _pick(block, "creditCostPerItem", "credit_cost_per_item", default=0),
        "position": block.get("position"),
        "edges_out": [
            {
                "to": e.get("toBlockId"),
                "src_handle": e.get("edge_source_handle_condition"),
                "tgt_handle": e.get("edge_target_handle_condition"),
            }
            for e in (block.get("toBlocks") or [])
        ],
    }


def slim_workflow(wf: dict) -> dict:
    blocks = shapes.blocks_of(wf)
    return {
        "id": wf.get("id") or wf.get("workflow_id"),
        "name": wf.get("name"),
        "description": wf.get("description"),
        "status": wf.get("status"),
        "config_error": _pick(wf, "workflowConfigError", "workflow_config_error"),
        "is_runable": _pick(wf, "isRunable", "is_runable"),
        "node_count": len(blocks),
        "nodes": [slim_block(b) for b in blocks],
    }


def scan_validation(wf: dict) -> dict:
    """Client-side validation scan: config errors + Magic Node reference
    sanity (refs must be edge IDs that actually exist)."""
    node_errors = []
    magic_warnings = []
    edge_ids = shapes.all_edge_ids(wf)
    for b in shapes.blocks_of(wf):
        if b.get("node_config_error"):
            node_errors.append({"node_id": b.get("id"), "name": shapes.block_name(b), "error": b["node_config_error"]})
        if _pick(b, "typeId", "node_definition_id") == shapes.MAGIC_NODE:
            refs, _ = shapes.read_magic_references(b)
            for r in refs:
                if r not in edge_ids:
                    magic_warnings.append(
                        {"node_id": b.get("id"), "reference": r, "problem": "not an existing edge id"}
                    )
    wf_error = _pick(wf, "workflowConfigError", "workflow_config_error")
    return {
        "valid": not wf_error and not node_errors and not magic_warnings,
        "is_runable": _pick(wf, "isRunable", "is_runable"),
        "workflow_config_error": wf_error,
        "node_errors": node_errors,
        "magic_reference_warnings": magic_warnings,
    }


# ── Node definitions ─────────────────────────────────────────────────────────

_MAX_INLINE_OPTIONS = 25


def slim_definition_item(d: dict) -> dict:
    return {
        "node_definition_id": _pick(d, "node_definition_id", "nodeDefinitionId", "id"),
        "type_slug": _pick(d, "value", "node_type_id"),
        "name": _pick(d, "name", "node_type_name"),
        "category": d.get("category"),
        "description": (d.get("description") or "")[:300],
        "is_trigger": _pick(d, "is_trigger", "isTrigger", default=False),
        "is_listener": _pick(d, "is_listener", "isListener", default=False),
    }


def _slim_schema_field(f: dict) -> dict:
    out = {
        "name": f.get("name"),
        "label": f.get("label"),
        "type": f.get("type"),
        "required": f.get("required"),
        "placeholder": f.get("placeholder"),
        "default": _pick(f, "defaultValue", "default_value"),
        "conditional_visibility": _pick(f, "conditionalVisibility", "conditional_visibility"),
    }
    options = f.get("options")
    if isinstance(options, list) and options:
        out["options"] = options[:_MAX_INLINE_OPTIONS]
        if len(options) > _MAX_INLINE_OPTIONS:
            out["options_truncated"] = len(options)
    # Dropdowns whose values come from an endpoint → fetch via get_field_options.
    sources = []
    for it in f.get("inputTypes") or []:
        ds = (it or {}).get("dataSource") or {}
        if ds.get("endpoint"):
            sources.append(ds["endpoint"])
    if sources:
        out["dynamic_options_via"] = "get_field_options"
    return {k: v for k, v in out.items() if v is not None}


def slim_definition_detail(d: dict) -> dict:
    schema = _pick(d, "settings", "settings_schema", default=[]) or []
    out = {
        "node_definition_id": _pick(d, "node_definition_id", "nodeDefinitionId", "id"),
        "type_slug": _pick(d, "value", "node_type_id"),
        "name": _pick(d, "name", "node_type_name"),
        "category": d.get("category"),
        "description": d.get("description"),
        "is_trigger": _pick(d, "is_trigger", "isTrigger", default=False),
        "is_listener": _pick(d, "is_listener", "isListener", default=False),
        "settings_fields": [_slim_schema_field(f) for f in schema if isinstance(f, dict)],
        "ai_metadata": d.get("ai_metadata"),
        "expected_output_columns": d.get("expected_output_columns"),
    }
    return {k: v for k, v in out.items() if v is not None}


def fields_needing_options(detail: dict) -> list[str]:
    """Field names in a slim definition whose dropdown values come from an
    endpoint (flagged `dynamic_options_via` by `_slim_schema_field`). These are
    the fields `describe_node` should pre-fetch options for in one shot."""
    return [
        f["name"]
        for f in detail.get("settings_fields") or []
        if f.get("dynamic_options_via") and f.get("name")
    ]


# ── Cost estimation ───────────────────────────────────────────────────────────


def estimate_cost(blocks: list[dict], rows: int) -> dict:
    """Upper-bound credit estimate for a full run, from each block's
    `creditCostPerItem` × `rows`. This is a CEILING: it assumes all `rows`
    reach every node — filters, dedup, and qualification splits reduce the real
    figure. Returns the total, a per-node breakdown, and the top cost drivers."""
    rows = max(0, int(rows))
    per_node = []
    for b in blocks:
        cost = _pick(b, "creditCostPerItem", "credit_cost_per_item", default=0) or 0
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            cost = 0.0
        per_node.append(
            {
                "node_id": b.get("id"),
                "name": _pick(b, "variableName", "variable_name", default=b.get("id")),
                "credit_cost_per_item": cost,
                "est_credits": round(cost * rows, 2),
            }
        )
    total = round(sum(n["est_credits"] for n in per_node), 2)
    drivers = sorted(
        (n for n in per_node if n["est_credits"] > 0),
        key=lambda n: n["est_credits"],
        reverse=True,
    )[:3]
    return {
        "rows_assumed": rows,
        "estimated_credits_max": total,
        "per_node": per_node,
        "cost_drivers": drivers,
        "note": (
            "Upper bound: assumes all rows_assumed rows reach every node. "
            "Filters/dedup/qualification splits reduce actual spend. Per-row "
            "costs come from the node catalog and can vary at runtime."
        ),
    }


# ── Executions ───────────────────────────────────────────────────────────────

_RUNNING_STATUSES = {"pending", "running", "queued", "in_progress"}


def _node_run_row_count(output: Any) -> Optional[int]:
    """Total rows a node run emitted — summed over its output items (one per
    output handle). The platform reports it at output[].file_info.rows_count."""
    if not isinstance(output, list):
        return None
    total, seen = 0, False
    for item in output:
        if not isinstance(item, dict):
            continue
        fi = _pick(item, "file_info", "fileInfo", default={}) or {}
        rc = _pick(fi, "rows_count", "rowsCount") if isinstance(fi, dict) else None
        if isinstance(rc, int):
            total += rc
            seen = True
    return total if seen else None


def slim_execution(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {"raw": raw}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    out = {
        "execution_id": _pick(data, "execution_id", "executionId", "id"),
        "status": data.get("status"),
        "started_at": _pick(data, "started_at", "startedAt"),
        "ended_at": _pick(data, "ended_at", "endedAt"),
        "duration": data.get("duration"),
        "credits_used": _pick(data, "credits_used", "creditsUsed"),
        "node_execution_count": _pick(data, "node_execution_count", "nodeExecutionCount"),
        "triggered_by": _pick(data, "execution_triggered_by", "executionTriggeredBy"),
        "error": _pick(data, "error_data", "errorData", "error_message", "error"),
    }
    # Per-node-RUN breakdown. The platform returns this under `blockRuns`
    # (Pydantic field node_execution_logs, serialized by alias). One entry PER
    # RUN — a node that runs many times (loops/fan-out) appears once per run,
    # each with its own node_execution_id. Pass that id to get_node_output to
    # read that specific run's rows.
    runs = _pick(data, "blockRuns", "node_execution_logs", "node_executions", "nodeExecutions", default=None)
    if isinstance(runs, list):
        out["node_runs"] = [
            {
                k: v
                for k, v in {
                    "node_execution_id": _pick(n, "node_execution_id", "nodeExecutionId", "id"),
                    "node_id": _pick(n, "node_id", "nodeId", "workflowBlockId"),
                    "node_name": _pick(n, "node_name", "workflowBlockName", "variableName", "variable_name"),
                    "status": n.get("status"),
                    "duration": n.get("duration"),
                    "credits_used": _pick(n, "credits_used", "creditsUsed"),
                    "row_count": _node_run_row_count(_pick(n, "output", "output_data", "outputData")),
                    "is_test_mode": _pick(n, "is_test_mode", "isTestMode"),
                    "started_at": _pick(n, "started_at", "startedAt"),
                    "ended_at": _pick(n, "ended_at", "endedAt"),
                    "error": _pick(n, "error", "error_message", "errorMessage"),
                }.items()
                if v is not None
            }
            for n in runs
            if isinstance(n, dict)
        ]
    out["is_running"] = str(out.get("status") or "").lower() in _RUNNING_STATUSES
    return {k: v for k, v in out.items() if v is not None}


def extract_execution_id(raw: Any) -> Optional[str]:
    """Tolerantly pull an execution id from any of the platform's launch
    response shapes ({execution:{response:{id}}}, {id}, {execution_id}, ...)."""
    if not isinstance(raw, dict):
        return None
    for path in (
        ("execution", "response", "id"),
        ("execution", "id"),
        ("response", "id"),
        ("data", "execution_id"),
        ("data", "id"),
        ("execution_id",),
        ("executionId",),
        ("id",),
    ):
        node: Any = raw
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, (str, int)):
            return str(node)
    return None


# ── Tables ───────────────────────────────────────────────────────────────────


def slim_table(t: dict) -> dict:
    cols = t.get("columns") or []
    return {
        "id": _pick(t, "id", "table_id"),
        "name": t.get("name"),
        "row_count": _pick(t, "row_count", "rowCount"),
        "columns": [
            {"id": _pick(c, "id", "column_id"), "name": c.get("name"), "type": c.get("type")}
            for c in cols
            if isinstance(c, dict)
        ],
    }
