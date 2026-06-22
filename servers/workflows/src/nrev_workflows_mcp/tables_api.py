"""Thin wrappers over the nRev tables service.

Separate host from the workflow API; same Supabase JWT. Quirks verified by the
predecessor project against production:

  - `limit` on GET /tables/{id}/rows accepts ONLY 100 / 500 / 1000 — anything
    else 422s, so we snap to the nearest allowed value.
  - `sortBy` on GET /tables was broken server-side; we don't expose it.
  - DELETE endpoints were "not yet live" (405) as of the predecessor's last
    test — delete_table surfaces the 405 with that context if it persists.
"""
from __future__ import annotations

from typing import Any, Optional

from . import config
from .transport import request as _request

_ALLOWED_ROW_LIMITS = (100, 500, 1000)


def request(method: str, path: str, json_body: Optional[dict] = None, params: Optional[dict] = None) -> Any:
    return _request(config.tables_host(), method, path, json_body=json_body, params=params)


def snap_row_limit(limit: int) -> int:
    limit = int(limit)
    for allowed in _ALLOWED_ROW_LIMITS:
        if limit <= allowed:
            return allowed
    return _ALLOWED_ROW_LIMITS[-1]


def list_tables(name: Optional[str] = None, skip: int = 0, limit: int = 100) -> dict:
    params: dict = {"skip": max(0, int(skip)), "limit": int(limit)}
    if name:
        params["name"] = name
    return request("GET", "/tables", params=params)


def get_table(table_id: str) -> dict:
    return request("GET", f"/tables/{table_id}")


def create_table(name: str, columns: Optional[list[dict]] = None) -> dict:
    """columns: [{name, type, position?}] — types: text | long_text | number |
    boolean | date | datetime | json."""
    body: dict = {"name": name}
    if columns:
        body["columns"] = columns
    return request("POST", "/tables", json_body=body)


def rename_table(table_id: str, new_name: str) -> dict:
    return request("PATCH", f"/tables/{table_id}", json_body={"name": new_name})


def delete_table(table_id: str) -> Any:
    return request("DELETE", f"/tables/{table_id}")


def add_column(table_id: str, name: str, col_type: str, position: Optional[int] = None) -> dict:
    body: dict = {"name": name, "type": col_type}
    if position is not None:
        body["position"] = int(position)
    return request("POST", f"/tables/{table_id}/columns", json_body=body)


def rename_column(table_id: str, column_id: str, new_name: str) -> dict:
    return request("PATCH", f"/tables/{table_id}/columns/{column_id}", json_body={"name": new_name})


def list_rows(
    table_id: str,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    filter_: Optional[dict] = None,
) -> dict:
    params: dict = {"skip": max(0, int(skip)), "limit": snap_row_limit(limit)}
    if search:
        params["search"] = search
    if filter_:
        # {column, operator, value} — column accepts name or id depending on
        # server version; pass through as provided.
        params.update({f"filter[{k}]": v for k, v in filter_.items()})
    return request("GET", f"/tables/{table_id}/rows", params=params)


def add_row(table_id: str, values: dict) -> dict:
    # `values` MUST be keyed by column_id (UUID) — the service 400s on a column
    # NAME ("Unknown column ..."). Callers translate names→ids first.
    return request("POST", f"/tables/{table_id}/rows", json_body={"values": values})


def update_row(table_id: str, row_id: int, values: dict) -> dict:
    """PATCH /tables/{table_id}/rows/{row_id} — merge-update one existing row.
    Same column_id-keyed `values` as add_row; PATCH semantics — only the listed
    cells change, others keep their value, and `null` clears a cell."""
    return request("PATCH", f"/tables/{table_id}/rows/{int(row_id)}", json_body={"values": values})


def bulk_delete_rows(table_id: str, row_ids: list[int]) -> dict:
    """POST /tables/{table_id}/rows/bulk-delete — hard-delete up to 1000 rows in
    one atomic call. Missing ids are silently skipped. Returns
    {deleted_row_ids, table: {row_count, last_updated_at}}."""
    return request(
        "POST",
        f"/tables/{table_id}/rows/bulk-delete",
        json_body={"row_ids": [int(r) for r in row_ids]},
    )


# ── Analytical endpoints (server-side; avoid pulling rows into context) ───────
# Body-encoded filter clauses here use {column_id, operator, value:[...]} — NOTE
# this differs from list_rows' bracketed query filter (operator not op, value
# always a list). Verified against the tables service M2 contract.


def aggregate(
    table_id: str,
    measures: list[dict],
    group_by: Optional[list[dict]] = None,
    filter_: Optional[list[dict]] = None,
    sort: Optional[list[dict]] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
) -> dict:
    """POST /tables/{table_id}/aggregate. (joins are deferred server-side — they
    400 with too_many_joins — so this wrapper doesn't send them; use
    join_tables for cross-table work.)"""
    body: dict = {"measures": measures}
    if group_by:
        body["group_by"] = group_by
    if filter_:
        body["filter"] = filter_
    if sort:
        body["sort"] = sort
    if limit is not None:
        body["limit"] = int(limit)
    if skip is not None:
        body["skip"] = int(skip)
    return request("POST", f"/tables/{table_id}/aggregate", json_body=body)


def distinct_values(
    table_id: str,
    column_id: str,
    filter_: Optional[list[dict]] = None,
    search: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """POST /tables/{table_id}/columns/{column_id}/distinct-values."""
    body: dict = {}
    if filter_:
        body["filter"] = filter_
    if search is not None:
        body["search"] = search
    if limit is not None:
        body["limit"] = int(limit)
    return request("POST", f"/tables/{table_id}/columns/{column_id}/distinct-values", json_body=body)


def join_tables(
    base_table_id: str,
    joins: list[dict],
    base_filter: Optional[list[dict]] = None,
    select: Optional[list[dict]] = None,
    sort: Optional[list[dict]] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
) -> dict:
    """POST /tables/{base_table_id}/join. Rows come back prefix-keyed
    (base.<col_id> / j0.<col_id> / …); the tool layer rewrites to names."""
    body: dict = {"joins": joins}
    if base_filter:
        body["base_filter"] = base_filter
    if select:
        body["select"] = select
    if sort:
        body["sort"] = sort
    if limit is not None:
        body["limit"] = int(limit)
    if skip is not None:
        body["skip"] = int(skip)
    return request("POST", f"/tables/{base_table_id}/join", json_body=body)
