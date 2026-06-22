"""nRev tables tools — the lightweight database workflows read and write.

Deliberately a small essential set (not the full tables API): enough to create
a table for a workflow to write into, seed small datasets, inspect schema and
rows, and clean up. Workflows interact with tables through the nrev_tables.*
nodes (Query Table / Add Row / Update Row / Get Row).
"""
from __future__ import annotations

from typing import Optional

from . import projections, tables_api, tenant
from .app import mcp


@mcp.tool()
def list_tables(search: Optional[str] = None, limit: int = 100, offset: int = 0) -> dict:
    """List nRev tables in the tenant (id, name, columns). `search` matches
    the table name. Use to find the table a workflow should read/write before
    configuring an nrev_tables node."""
    raw = tables_api.list_tables(name=search, skip=offset, limit=limit)
    data = raw.get("data") if isinstance(raw, dict) else raw
    return {"tables": [projections.slim_table(t) for t in (data or []) if isinstance(t, dict)]}


@mcp.tool()
def get_table(table_id: str) -> dict:
    """Get a table's full schema — column ids, names, and types. Column IDs
    (not names) are what nrev_tables node settings reference; get them here or
    from get_field_options on the node."""
    return projections.slim_table(tables_api.get_table(table_id))


@mcp.tool()
def create_table(table_name: str, columns: list[dict]) -> dict:
    """Create a new nRev table, typically as the destination a workflow writes
    results into (wire an nrev_tables Add Row / Update Row node to it), or as
    a seed-data source (fill with add_table_rows, read with a Query Table
    node).

    `columns`: [{"name": ..., "type": ...}] — types: text | long_text |
    number | boolean | date | datetime | json. Use snake_case column names
    (they become {{template}} identifiers in node settings — spaces break
    template resolution).
    """
    # New resource — no existing id for the backend to access-gate, so a
    # mid-flight tenant switch would silently create this in the wrong tenant.
    tenant.assert_pinned_active("creating a table")
    return projections.slim_table(tables_api.create_table(table_name, columns))


@mcp.tool()
def update_table(
    table_id: str,
    rename_to: Optional[str] = None,
    add_columns: Optional[list[dict]] = None,
    rename_column: Optional[dict] = None,
) -> dict:
    """Modify a table: rename it, append columns ([{"name","type"}]), and/or
    rename one column ({"column_id": ..., "new_name": ...}). Returns the
    updated schema."""
    if not any([rename_to, add_columns, rename_column]):
        raise ValueError("pass at least one of rename_to / add_columns / rename_column")
    if rename_to:
        tables_api.rename_table(table_id, rename_to)
    for col in add_columns or []:
        tables_api.add_column(table_id, col["name"], col["type"], col.get("position"))
    if rename_column:
        tables_api.rename_column(table_id, rename_column["column_id"], rename_column["new_name"])
    return projections.slim_table(tables_api.get_table(table_id))


@mcp.tool()
def delete_table(table_id: str, confirm: bool = False) -> dict:
    """Delete a table and all its rows. Destructive — requires confirm=true,
    and you should name the table to the user before calling. (If the platform
    returns 405, the delete endpoint isn't live in this environment yet —
    tell the user to delete via the UI.)"""
    if not confirm:
        raise ValueError("delete_table is destructive — call again with confirm=true")
    tables_api.delete_table(table_id)
    return {"deleted": table_id}


@mcp.tool()
def get_table_rows(
    table_id: str,
    search: Optional[str] = None,
    filter: Optional[dict] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Read rows from a table — verify what a workflow wrote, or inspect seed
    data. `filter`: {"column": <name-or-id>, "operator": ..., "value": ...}.
    Note the service only accepts page sizes 100/500/1000 (auto-snapped)."""
    return tables_api.list_rows(table_id, skip=offset, limit=limit, search=search, filter_=filter)


def _column_resolver(table_id: str):
    """Build a {column-name-or-id → column_id} resolver from a table's schema.

    The tables service keys row `values` by column_id and 400s on a column
    NAME, so add/update must translate first. Mirrors the platform's nodes:
    a key that matches a column id is kept as-is, else it's matched against
    column names case-insensitively; an unresolved key raises with the
    available names listed.
    """
    cols = projections.slim_table(tables_api.get_table(table_id)).get("columns") or []
    ids = {c["id"] for c in cols if c.get("id")}
    by_name = {(c.get("name") or "").strip().lower(): c["id"] for c in cols if c.get("id") and c.get("name")}
    names = [c.get("name") for c in cols if c.get("name")]

    def resolve(values: dict) -> dict:
        out: dict = {}
        for key, val in (values or {}).items():
            if key in ids:
                out[key] = val
                continue
            cid = by_name.get(str(key).strip().lower())
            if cid is None:
                raise ValueError(f"unknown column {key!r} — table columns: {', '.join(names) or '(none)'}")
            out[cid] = val
        return out

    return resolve


def _column_maps(table_id: str) -> tuple[dict, dict, str]:
    """(id→name, lowercased-name→id, table_name) for a table — used by the
    analytical tools to accept column names and to rename id-keyed responses."""
    t = projections.slim_table(tables_api.get_table(table_id))
    cols = t.get("columns") or []
    id_to_name = {c["id"]: c.get("name") for c in cols if c.get("id")}
    name_to_id = {(c.get("name") or "").strip().lower(): c["id"] for c in cols if c.get("id") and c.get("name")}
    return id_to_name, name_to_id, t.get("name") or "base"


@mcp.tool()
def add_table_rows(table_id: str, rows: list[dict]) -> dict:
    """Insert rows ([{column: value}]) — the way to seed a small dataset
    (≲100 rows) for a workflow to consume via a Query Table node. Column keys
    may be column NAMES or column ids (names are resolved to ids, which the
    service requires). Inserts are one API call per row; for large datasets
    have the user upload a CSV in the platform UI instead.

    Values must match column types (numbers as numbers, not strings). Returns
    per-row results; failed rows carry the error so you can fix and retry just
    those.
    """
    if not rows:
        return {"inserted": 0, "failed": 0, "results": []}
    resolve = _column_resolver(table_id)
    results = []
    for i, row in enumerate(rows):
        try:
            tables_api.add_row(table_id, resolve(row))
            results.append({"row": i, "ok": True})
        except Exception as exc:
            results.append({"row": i, "ok": False, "error": str(exc)})
    ok = sum(1 for r in results if r["ok"])
    return {"inserted": ok, "failed": len(results) - ok, "results": results}


@mcp.tool()
def update_table_rows(table_id: str, updates: list[dict]) -> dict:
    """Update existing rows in place — a cell-level PATCH. This is how you flip
    a flag (e.g. is_archived / "Connection Removed"), fix a value, or clear a
    cell on a row that already exists, without re-inserting it.

    `updates`: [{"row_id": <int>, "values": {column: value}}]. Only the listed
    cells change; other cells keep their values; pass null to clear a cell.
    Column keys may be names or ids (resolved to ids). Get each row's `row_id`
    from get_table_rows. One API call per row; returns per-row results with the
    error on any that failed so you can retry just those.
    """
    if not updates:
        return {"updated": 0, "failed": 0, "results": []}
    resolve = _column_resolver(table_id)
    results = []
    for upd in updates:
        row_id = upd.get("row_id")
        try:
            if row_id is None:
                raise ValueError("each update needs a 'row_id'")
            tables_api.update_row(table_id, row_id, resolve(upd.get("values") or {}))
            results.append({"row_id": row_id, "ok": True})
        except Exception as exc:
            results.append({"row_id": row_id, "ok": False, "error": str(exc)})
    ok = sum(1 for r in results if r["ok"])
    return {"updated": ok, "failed": len(results) - ok, "results": results}


@mcp.tool()
def delete_table_rows(table_id: str, row_ids: list[int], confirm: bool = False) -> dict:
    """Permanently delete rows by row_id — a hard delete (up to 1000 per call).
    Destructive, so it requires confirm=true; name what you're deleting to the
    user first. Missing ids are silently skipped. Get row_ids from
    get_table_rows. Returns the ids actually deleted and the new row_count."""
    if not confirm:
        raise ValueError("delete_table_rows is destructive — call again with confirm=true")
    if not row_ids:
        return {"deleted": 0, "deleted_row_ids": []}
    resp = tables_api.bulk_delete_rows(table_id, row_ids)
    deleted = resp.get("deleted_row_ids") if isinstance(resp, dict) else None
    return {
        "deleted": len(deleted) if isinstance(deleted, list) else None,
        "deleted_row_ids": deleted,
        "table": resp.get("table") if isinstance(resp, dict) else None,
    }


# ── Analytical reads — compute over a whole table WITHOUT pulling rows into
# context. The `filter` arg on these takes [{column_id, operator, value:[...]}]
# clauses — NOTE this is NOT the get_table_rows filter shape: the key is
# `operator` (not `op`), `value` is ALWAYS a list (e.g. [42]), and booleans are
# lowercase strings ("true"). operators: eq neq contains gt gte lt lte is_empty
# is_not_empty in not_in. Column ids come from get_table.


@mcp.tool()
def aggregate_table(
    table_id: str,
    measures: list[dict],
    group_by: Optional[list[dict]] = None,
    filter: Optional[list[dict]] = None,
    sort: Optional[list[dict]] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    resolve_names: bool = True,
) -> dict:
    """Server-side count / count_distinct / sum / avg / min / max over a table,
    with optional group_by — the "I have thousands of rows and want stats /
    group-bys / dedup counts without paginating them into context" tool.

    `measures`: [{"op": ..., "column_id": ..., "alias": ...}] — ops: count (no
    column_id needed), count_distinct, sum, avg, min, max. `group_by`:
    [{"column_id": ...}]. `filter`: see the module note above (operator/value-
    as-list shape, NOT the get_table_rows shape). For cross-table aggregation,
    join first with join_tables. `resolve_names` rewrites the response group
    keys from column ids to names. Returns {groups:[{keys,measures}], meta}.
    """
    if not measures:
        raise ValueError("measures is required and non-empty")
    resp = tables_api.aggregate(
        table_id, measures, group_by=group_by, filter_=filter, sort=sort, limit=limit, skip=skip
    )
    if not resolve_names or not isinstance(resp, dict):
        return resp
    id_to_name, _, _ = _column_maps(table_id)
    groups = [
        {"keys": {id_to_name.get(k, k): v for k, v in (g.get("keys") or {}).items()},
         "measures": g.get("measures") or {}}
        for g in (resp.get("groups") or [])
    ]
    return {"groups": groups, "meta": resp.get("meta")}


@mcp.tool()
def get_distinct_values(
    table_id: str,
    column: str,
    filter: Optional[list[dict]] = None,
    search: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """Distinct values of one column — "what categories does this column
    actually have?" `column` may be a column name or id. `filter` narrows the
    universe first (operator/value-as-list shape — see the module note).
    `search` is a case-insensitive substring on the value. Returns
    {values:[...], meta:{total_distinct, truncated}}."""
    id_to_name, name_to_id, _ = _column_maps(table_id)
    column_id = column if column in id_to_name else name_to_id.get(str(column).strip().lower())
    if not column_id:
        names = ", ".join(n for n in id_to_name.values() if n) or "(none)"
        raise ValueError(f"unknown column {column!r} — table columns: {names}")
    return tables_api.distinct_values(table_id, column_id, filter_=filter, search=search, limit=limit)


@mcp.tool()
def join_tables(
    base_table_id: str,
    joins: list[dict],
    base_filter: Optional[list[dict]] = None,
    select: Optional[list[dict]] = None,
    sort: Optional[list[dict]] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    resolve_names: bool = True,
) -> dict:
    """Server-side inner/left join across tables (up to 3 joined) — combine
    related tables without fetching and stitching rows yourself.

    `joins`: [{"type": "inner"|"left", "table_id": ...,
    "on": {"base_column_id": ..., "joined_column_id": ...}}] — `on` is a single
    dict (single-column joins). `base_filter`: clauses on the base table (see
    the module note). `select`: [{"table_id", "column_id"}] projection (omit for
    all columns). Column ids come from get_table on each side. `resolve_names`
    rewrites the prefix-keyed response rows (base.<id>, j0.<id>, …) to column
    names, prefixing with the table name only where a name collides across
    tables. Returns {rows:[...], meta}.
    """
    if not joins:
        raise ValueError("joins is required and non-empty")
    resp = tables_api.join_tables(
        base_table_id, joins, base_filter=base_filter, select=select, sort=sort, limit=limit, skip=skip
    )
    if not resolve_names or not isinstance(resp, dict):
        return resp

    base_id_to_name, _, base_name = _column_maps(base_table_id)
    joined: list[tuple[str, dict]] = []
    for j in joins:
        tid = j.get("table_id") if isinstance(j, dict) else None
        if not tid:
            joined.append(("?", {}))
            continue
        try:
            id_to_name, _, tname = _column_maps(tid)
        except Exception:
            id_to_name, tname = {}, tid
        joined.append((tname, id_to_name))

    counts: dict = {}
    for nm in base_id_to_name.values():
        counts[nm] = counts.get(nm, 0) + 1
    for _tn, id_to_name in joined:
        for nm in id_to_name.values():
            counts[nm] = counts.get(nm, 0) + 1

    def _rewrite(key: str) -> str:
        if "." not in key:
            return key
        prefix, col_id = key.split(".", 1)
        if prefix == "base":
            name = base_id_to_name.get(col_id, col_id)
            return f"{base_name}.{name}" if name and counts.get(name, 0) > 1 else (name or col_id)
        try:
            tname, id_to_name = joined[int(prefix[1:])]
        except (ValueError, IndexError):
            return key
        name = id_to_name.get(col_id, col_id)
        return f"{tname}.{name}" if name and counts.get(name, 0) > 1 else (name or col_id)

    rows = [{_rewrite(k): v for k, v in r.items()} for r in (resp.get("rows") or [])]
    return {"rows": rows, "meta": resp.get("meta")}
