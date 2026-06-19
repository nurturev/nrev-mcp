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


@mcp.tool()
def add_table_rows(table_id: str, rows: list[dict]) -> dict:
    """Insert rows ([{column_name: value}]) — the way to seed a small dataset
    (≲100 rows) for a workflow to consume via a Query Table node. Inserts are
    one API call per row; for large datasets have the user upload a CSV in the
    platform UI instead.

    Values must match column types (numbers as numbers, not strings). Returns
    per-row results; failed rows carry the error so you can fix and retry just
    those.
    """
    results = []
    for i, row in enumerate(rows):
        try:
            tables_api.add_row(table_id, row)
            results.append({"row": i, "ok": True})
        except Exception as exc:
            results.append({"row": i, "ok": False, "error": str(exc)})
    ok = sum(1 for r in results if r["ok"])
    return {"inserted": ok, "failed": len(results) - ok, "results": results}
