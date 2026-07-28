"""nRev tables tools — the lightweight database workflows read and write.

Deliberately a small essential set (not the full tables API): enough to create
a table for a workflow to write into, seed small datasets, inspect schema and
rows, and clean up. Workflows interact with tables through the nrev_tables.*
nodes (Query Table / Add Row / Update Row / Get Row).
"""
from __future__ import annotations

import json
from typing import Optional

from . import projections, tables_api, tenant
from .app import mcp


@mcp.tool()
def list_tables(
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    tag_ids: Optional[list[str]] = None,
) -> dict:
    """List nRev tables in the tenant (id, name, columns, tag_ids). `search`
    matches the table name. `tag_ids` filters to tables carrying ANY of the
    listed tag ids (OR) — get ids from list_tags. Use to find the table a
    workflow should read/write before configuring an nrev_tables node."""
    raw = tables_api.list_tables(name=search, skip=offset, limit=limit, tag_ids=tag_ids)
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
def set_table_tags(table_id: str, tag_ids: list[str]) -> dict:
    """Set a table's tags to EXACTLY this list (converge, not additive) — `[]`
    detaches every tag. Get tag ids from list_tags / find_or_create_tag. For
    add/remove-one semantics use add_table_tag / remove_table_tag instead,
    which read the current set for you."""
    return projections.slim_table(tables_api.set_table_tags(table_id, tag_ids))


@mcp.tool()
def add_table_tag(table_id: str, tag_id: str) -> dict:
    """Attach one tag to a table without disturbing its other tags (reads the
    current tag_ids, adds this one, writes the full set back — the backend
    itself has no additive primitive)."""
    current = set(projections.slim_table(tables_api.get_table(table_id)).get("tag_ids") or [])
    current.add(tag_id)
    return projections.slim_table(tables_api.set_table_tags(table_id, sorted(current)))


@mcp.tool()
def remove_table_tag(table_id: str, tag_id: str) -> dict:
    """Detach one tag from a table without disturbing its other tags (reads
    the current tag_ids, drops this one, writes the full set back)."""
    current = set(projections.slim_table(tables_api.get_table(table_id)).get("tag_ids") or [])
    current.discard(tag_id)
    return projections.slim_table(tables_api.set_table_tags(table_id, sorted(current)))


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
def duplicate_table(table_id: str, new_name: Optional[str] = None, include_rows: bool = False) -> dict:
    """Clone a table's schema (columns, unique constraints, tags) into a new
    table. Schema-only by default — pass include_rows=true to also copy the
    row data. Without new_name the platform auto-suffixes on a name collision
    (same behavior as create_table), so the copy just gets "<name> 2" etc.

    No dedicated backend endpoint exists for this — it's composed from
    schema/export + schema/import (+ paginated row copy when include_rows is
    set), so it may take a few seconds on a table with many rows/columns.
    """
    # New resource — no existing id for the backend to access-gate, so a
    # mid-flight tenant switch would silently create this in the wrong tenant.
    tenant.assert_pinned_active("duplicating a table")
    exported = tables_api.export_table_schema(table_id)
    expression = exported.get("expression") if isinstance(exported, dict) else None
    if not expression:
        raise ValueError(f"schema export for table {table_id} returned no expression")
    if new_name:
        parsed = json.loads(expression)
        parsed["table_name"] = new_name
        expression = json.dumps(parsed)
    new_table = projections.slim_table(tables_api.import_table_schema(expression))
    new_table_id = new_table.get("id")

    rows_copied = 0
    rows_failed = 0
    if include_rows and new_table_id:
        # Exclude system columns (row_id/added_at/last_updated_at) from the
        # remap entirely — the backend 400s a bulk insert that includes any
        # system-field column id (they're stamped server-side, per-table).
        source_cols = projections.slim_table(tables_api.get_table(table_id)).get("columns") or []
        new_cols = projections.slim_table(tables_api.get_table(new_table_id)).get("columns") or []
        source_id_to_name = {
            c["id"]: (c.get("name") or "").strip().lower()
            for c in source_cols
            if c.get("id") and not c.get("is_system")
        }
        new_name_to_id = {
            (c.get("name") or "").strip().lower(): c["id"]
            for c in new_cols
            if c.get("id") and not c.get("is_system")
        }
        # Page size matches MAX_ROWS_PER_BULK_INSERT (500) 1:1 so each page
        # maps to exactly one bulk_insert_rows call — list_rows only accepts
        # 100/500/1000 anyway, and 1000 would 400 on the insert side.
        skip, page = 0, 500
        while True:
            page_resp = tables_api.list_rows(table_id, skip=skip, limit=page)
            rows = page_resp.get("data") if isinstance(page_resp, dict) else page_resp
            if not rows:
                break
            batch = []
            for row in rows:
                values = row.get("values") if isinstance(row, dict) else None
                if not isinstance(values, dict):
                    continue
                remapped = {}
                for col_id, val in values.items():
                    name = source_id_to_name.get(col_id)
                    new_col_id = new_name_to_id.get(name) if name else None
                    if new_col_id:
                        remapped[new_col_id] = val
                batch.append(remapped)
            if batch:
                result = tables_api.bulk_insert_rows(new_table_id, batch)
                rejected = result.get("rejected") if isinstance(result, dict) else None
                n_rejected = len(rejected) if isinstance(rejected, list) else 0
                rows_copied += len(batch) - n_rejected
                rows_failed += n_rejected
            if len(rows) < page:
                break
            skip += page
        new_table = projections.slim_table(tables_api.get_table(new_table_id))

    out = dict(new_table)
    if include_rows:
        out["rows_copied"] = rows_copied
        out["rows_failed"] = rows_failed
    return out


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


@mcp.tool()
def clear_table_rows(table_id: str, confirm: bool = False) -> dict:
    """Delete ALL rows in a table, keeping its schema (columns) intact —
    "empty this table out" without touching its structure. Destructive,
    requires confirm=true; name the table to the user first.

    No dedicated "clear"/"truncate" endpoint exists on the backend — this
    pages through the table's rows and bulk-deletes them 1000 at a time until
    none remain, so on a very large table (cap: 1,000,000 rows) this is many
    sequential calls and may take a while. For "recreate this table empty"
    instead (different table_id), use duplicate_table without include_rows
    plus delete_table on the original.
    """
    if not confirm:
        raise ValueError("clear_table_rows is destructive — call again with confirm=true")
    total_deleted = 0
    last_table_meta = None
    while True:
        page = tables_api.list_rows(table_id, skip=0, limit=1000)
        rows = page.get("data") if isinstance(page, dict) else page
        row_ids = [r.get("row_id") for r in (rows or []) if isinstance(r, dict) and r.get("row_id") is not None]
        if not row_ids:
            break
        resp = tables_api.bulk_delete_rows(table_id, row_ids)
        deleted = resp.get("deleted_row_ids") if isinstance(resp, dict) else None
        total_deleted += len(deleted) if isinstance(deleted, list) else len(row_ids)
        last_table_meta = resp.get("table") if isinstance(resp, dict) else None
    return {"cleared": True, "rows_deleted": total_deleted, "table": last_table_meta}


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
