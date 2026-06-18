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
    return request("POST", f"/tables/{table_id}/rows", json_body={"values": values})
