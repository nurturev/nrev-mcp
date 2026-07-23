"""One-off data tools — federation of the workflow_studio MCP server.

The platform exposes each tool-eligible node (LinkedIn activity, company
signals, …) as an MCP tool on its own Streamable HTTP server at
``<workflow_host>/mcp`` (override: ``NREV_WF_MCP_URL``). This module makes
nrev-mcp a CLIENT of that server so those data tools appear in the user's
single chat surface: ``list_data_tools`` discovers what's available (the
upstream ``tools/list`` is authoritative — new eligible nodes appear here
automatically, nothing is hard-coded), ``run_data_tool`` forwards a call, and
``save_to_table`` lands the returned records in an nRev Table so a one-off
pull can graduate into a workflow-consumable dataset.

Upstream contract: tools are named ``<node_type_id with . replaced by __>``
(e.g. ``linkedin_scraping__get_person_profile``); every tool takes
``{settings: object, confirm: boolean}``; the SPEND GATE is enforced
server-side — with ``confirm=false`` the server returns a structured
"blocked" result carrying a credit estimate instead of running. Auth is the
same platform JWT every other call uses (``auth.get_jwt()``).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from . import auth, config, projections, tables_api, tenant, transport
from .app import mcp
from .tools_tables import _column_resolver

_UPSTREAM_TIMEOUT_SECONDS = 120


def _unreachable_error(url: str, exc: Exception) -> RuntimeError:
    return RuntimeError(
        f"the platform data server (workflow_studio /mcp) is not reachable at {url} — "
        f"it may not be deployed yet in this environment. One-off data tools are "
        f"unavailable until it is; workflows (run_workflow / run_node) still work. "
        f"Underlying error: {type(exc).__name__}: {exc}"
    )


def _looks_unauthorized(exc: Exception) -> bool:
    text = str(exc)
    return "401" in text or "unauthorized" in text.lower()


async def _with_upstream(fn):
    """Open a short-lived MCP session against the workflow_studio data server
    and run `fn(session)` in it. Auth mirrors transport.py: the platform JWT
    rides as a Bearer header, and an upstream 401 gets one force-refresh +
    retry before surfacing the standard sign-in guidance."""
    url = config.wf_mcp_url()

    async def _once(token: str):
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Nrev-Client": transport.CLIENT_SOURCE,
        }
        async with streamablehttp_client(url, headers=headers, timeout=_UPSTREAM_TIMEOUT_SECONDS) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    token = auth.get_jwt()  # raises AuthError with sign-in guidance when unset
    try:
        return await _once(token)
    except Exception as exc:  # noqa: BLE001 — classify and re-raise actionably
        if _looks_unauthorized(exc):
            new_token = auth.force_refresh()
            if new_token and new_token != token:
                try:
                    return await _once(new_token)
                except Exception as retry_exc:  # noqa: BLE001
                    if _looks_unauthorized(retry_exc):
                        raise auth.AuthError(
                            "the platform data server rejected the session token even "
                            "after a refresh. Run `nrev-workflows auth login` (or the "
                            "auth_login tool) to sign in again."
                        )
                    raise _unreachable_error(url, retry_exc)
            raise auth.AuthError(
                "the platform data server rejected the session token (401). Run "
                "`nrev-workflows auth login` (or the auth_login tool) to sign in again."
            )
        raise _unreachable_error(url, exc)


def _input_hints(schema: Any) -> Any:
    """Compact hint of a data tool's `settings` fields from its input schema —
    enough for the agent to shape a call without the full JSON Schema."""
    if not isinstance(schema, dict):
        return None
    settings = (schema.get("properties") or {}).get("settings") or {}
    props = settings.get("properties")
    if not isinstance(props, dict) or not props:
        return settings.get("description") or None
    required = set(settings.get("required") or [])
    return [
        {
            k: v
            for k, v in {
                "name": name,
                "type": (spec or {}).get("type"),
                "description": ((spec or {}).get("description") or "")[:200] or None,
                "required": True if name in required else None,
            }.items()
            if v is not None
        }
        for name, spec in props.items()
    ]


def _parse_tool_result(result: Any) -> Any:
    """Flatten a CallToolResult to plain JSON where possible: prefer the
    structured content; else parse each text block as JSON, falling back to
    the raw text."""
    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured
    parsed: list[Any] = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text is None:
            continue
        try:
            parsed.append(json.loads(text))
        except (ValueError, TypeError):
            parsed.append(text)
    if len(parsed) == 1:
        return parsed[0]
    return parsed or None


def _blocked_payload(parsed: Any) -> Optional[dict]:
    """Detect the upstream spend gate's structured "blocked" result (returned
    when confirm=false on a credit-costing call) and normalize it."""
    if isinstance(parsed, dict):
        candidates = [parsed]
    elif isinstance(parsed, list):
        candidates = [p for p in parsed if isinstance(p, dict)]
    else:
        candidates = []
    for c in candidates:
        status = str(c.get("status") or "").lower()
        if status == "blocked" or c.get("blocked") is True:
            return c
    return None


@mcp.tool()
async def list_data_tools() -> dict:
    """List the ONE-OFF DATA TOOLS the platform currently exposes — direct
    "get me this data now" calls (LinkedIn activity, company signals, …)
    backed by the same nodes workflows use, WITHOUT building a workflow. Each
    entry is run via run_data_tool(tool_name, settings); results can be
    persisted with save_to_table. The set is served live by the platform, so
    newly eligible nodes appear here automatically — call this rather than
    assuming which data tools exist.
    """
    async def _list(session):
        return await session.list_tools()

    listing = await _with_upstream(_list)
    tools = [
        {
            k: v
            for k, v in {
                "name": t.name,
                "description": (t.description or "").strip()[:300] or None,
                "settings_fields": _input_hints(getattr(t, "inputSchema", None)),
            }.items()
            if v is not None
        }
        for t in (getattr(listing, "tools", None) or [])
    ]
    return {
        "data_tools": tools,
        "note": (
            "These are one-off data tools — run one with run_data_tool(tool_name, "
            "settings). Executions spend tenant credits: a first call (confirm=false) "
            "returns a credit estimate instead of running; show it to the user, get "
            "their go-ahead, then re-call with confirm=true. Persist results with "
            "save_to_table."
        ),
    }


@mcp.tool()
async def run_data_tool(tool_name: str, settings: dict, confirm: bool = False) -> dict:
    """Run one of the platform's one-off data tools (from list_data_tools) —
    fetch data NOW without building a workflow. `settings` shapes the call
    (see the tool's settings_fields in list_data_tools).

    SPEND GATE (server-enforced): these calls cost tenant credits. With
    confirm=false (default) the platform returns a credit ESTIMATE instead of
    running — show the user the estimated credits, get their explicit
    go-ahead, then re-call with confirm=true. Never pass confirm=true on the
    first call for a new request.

    Returns the tool's records under `result` on success. Land them in an
    nRev Table with save_to_table so a workflow can consume them later.
    """
    async def _call(session):
        return await session.call_tool(tool_name, {"settings": settings or {}, "confirm": bool(confirm)})

    raw = await _with_upstream(_call)
    parsed = _parse_tool_result(raw)

    if getattr(raw, "isError", False):
        detail = parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False, default=str)
        raise RuntimeError(f"data tool {tool_name!r} failed upstream: {detail[:1000]}")

    blocked = _blocked_payload(parsed)
    if blocked is not None:
        return {
            "status": "blocked",
            "tool_name": tool_name,
            "estimate": blocked.get("estimate") or blocked.get("credit_estimate") or blocked.get("estimated_credits"),
            "detail": blocked,
            "next_step": (
                "This run spends real credits — show the user the estimate above and "
                "get their go-ahead, then re-call run_data_tool with the same settings "
                "and confirm=true."
            ),
        }
    return {"status": "ok", "tool_name": tool_name, "result": parsed}


# ── save_to_table — land one-off results in an nRev Table ────────────────────


def _derive_columns(rows: list[dict]) -> list[dict]:
    """Column specs from the union of row keys, in first-appearance order.
    Nested dict/list values → a `json` column; everything else `text` (the
    one-off results are provider payloads of unknown/mixed scalar types, and
    text accepts them all)."""
    order: list[str] = []
    is_json: dict[str, bool] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, val in row.items():
            if key not in is_json:
                order.append(key)
                is_json[key] = False
            if isinstance(val, (dict, list)):
                is_json[key] = True
    return [{"name": k, "type": "json" if is_json[k] else "text"} for k in order]


def _prepare_value(val: Any, col_type: Optional[str]) -> Any:
    """Coerce a row value to what the column type accepts: nested structures
    are JSON-serialized (json columns store the serialized form; the exact
    json-column contract is an open platform item), and scalars headed into a
    text column are stringified so provider payloads with mixed types insert
    cleanly."""
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False, default=str)
    if val is not None and not isinstance(val, str) and col_type in ("text", "long_text"):
        return str(val)
    return val


def _find_table_by_name(name: str) -> Optional[dict]:
    raw = tables_api.list_tables(name=name)
    data = raw.get("data") if isinstance(raw, dict) else raw
    want = name.strip().lower()
    for t in data or []:
        if isinstance(t, dict) and str(t.get("name") or "").strip().lower() == want:
            return t
    return None


@mcp.tool()
def save_to_table(
    rows: list[dict],
    table_name: Optional[str] = None,
    table_id: Optional[str] = None,
    create_if_missing: bool = True,
) -> dict:
    """Persist records (e.g. a run_data_tool result) into an nRev Table — the
    step that turns a one-off data pull into permanent, workflow-consumable
    storage ("get the data once → now automate it": workflows read the table
    via a Query Table node).

    Target resolution: `table_id` wins; else `table_name` is looked up, and —
    when not found and create_if_missing (default) — a new table is created
    with columns derived from the rows' keys (nested dict/list values become a
    `json` column, everything else `text`). Row keys may be column names or
    ids; values in nested structures are JSON-serialized. Returns per-row
    results with the error on any row that failed, so you can fix and retry
    just those.
    """
    if not rows:
        return {"table_id": table_id, "inserted": 0, "failed": 0, "results": []}
    if not table_id and not table_name:
        raise ValueError("pass table_id (existing table) or table_name (found or created)")

    created = False
    if not table_id:
        existing = _find_table_by_name(table_name)
        if existing is not None:
            table_id = existing.get("id") or existing.get("table_id")
        elif create_if_missing:
            # New resource — no existing id for the backend to access-gate, so a
            # mid-flight tenant switch would silently create this in the wrong tenant.
            tenant.assert_pinned_active("creating a table")
            made = tables_api.create_table(table_name, _derive_columns(rows))
            table_id = made.get("id") or made.get("table_id")
            created = True
        else:
            raise ValueError(
                f"no table named {table_name!r} — pass create_if_missing=true to create it, "
                f"or an explicit table_id"
            )

    resolve = _column_resolver(table_id)
    col_types = {
        c.get("id"): c.get("type")
        for c in (projections.slim_table(tables_api.get_table(table_id)).get("columns") or [])
    }
    results = []
    for i, row in enumerate(rows):
        try:
            values = {
                cid: _prepare_value(val, col_types.get(cid))
                for cid, val in resolve(row if isinstance(row, dict) else {"value": row}).items()
            }
            tables_api.add_row(table_id, values)
            results.append({"row": i, "ok": True})
        except Exception as exc:  # noqa: BLE001 — per-row error capture, like add_table_rows
            results.append({"row": i, "ok": False, "error": str(exc)})
    ok = sum(1 for r in results if r["ok"])
    return {
        "table_id": table_id,
        "created": created,
        "inserted": ok,
        "failed": len(results) - ok,
        "results": results,
    }
