"""Tests for the tables tools — column name→id resolution. Monkeypatched, no network."""
import pytest

from nrev_workflows_mcp import tables_api, tools_tables

_FAKE_TABLE = {
    "id": "t1",
    "name": "Connections",
    "columns": [
        {"id": "c1", "name": "Email", "type": "text"},
        {"id": "c2", "name": "Connection Removed", "type": "boolean"},
    ],
}


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(tables_api, "get_table", lambda table_id: _FAKE_TABLE)
    return tools_tables._column_resolver("t1")


def test_resolves_column_name_to_id(resolver):
    assert resolver({"Email": "a@b.com"}) == {"c1": "a@b.com"}


def test_name_match_is_case_insensitive(resolver):
    assert resolver({"connection removed": True}) == {"c2": True}


def test_existing_column_id_passes_through(resolver):
    assert resolver({"c2": True}) == {"c2": True}


def test_null_value_preserved_for_clearing_a_cell(resolver):
    assert resolver({"Email": None}) == {"c1": None}


def test_unknown_column_raises_with_available_names(resolver):
    with pytest.raises(ValueError) as exc:
        resolver({"Nope": 1})
    assert "Email" in str(exc.value) and "Connection Removed" in str(exc.value)


# ── analytical + delete tools ─────────────────────────────────────────────────

_FAKE_PEOPLE = {
    "id": "t2",
    "name": "People",
    "columns": [
        {"id": "d1", "name": "Email", "type": "text"},      # collides with base "Email"
        {"id": "d2", "name": "Title", "type": "text"},
    ],
}


@pytest.fixture
def two_tables(monkeypatch):
    by_id = {"t1": _FAKE_TABLE, "t2": _FAKE_PEOPLE}
    monkeypatch.setattr(tables_api, "get_table", lambda table_id: by_id[table_id])


def test_aggregate_resolves_group_keys_to_names(two_tables, monkeypatch):
    monkeypatch.setattr(
        tables_api, "aggregate",
        lambda *a, **k: {"groups": [{"keys": {"c2": True}, "measures": {"n": 5}}],
                         "meta": {"group_count": 1, "truncated": False}},
    )
    out = tools_tables.aggregate_table(
        "t1", measures=[{"op": "count", "alias": "n"}], group_by=[{"column_id": "c2"}]
    )
    assert out["groups"][0]["keys"] == {"Connection Removed": True}
    assert out["groups"][0]["measures"] == {"n": 5}


def test_distinct_values_resolves_column_name(two_tables, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        tables_api, "distinct_values",
        lambda table_id, column_id, **k: seen.update(column_id=column_id) or {"values": []},
    )
    tools_tables.get_distinct_values("t1", "Email")
    assert seen["column_id"] == "c1"  # name → id


def test_join_rewrites_prefix_keys_and_prefixes_collisions(two_tables, monkeypatch):
    monkeypatch.setattr(
        tables_api, "join_tables",
        lambda *a, **k: {
            "rows": [{"base.c1": "a@b.com", "base.c2": True, "j0.d1": "a@b.com", "j0.d2": "VP"}],
            "meta": {"total_entries": 1},
        },
    )
    out = tools_tables.join_tables(
        "t1",
        joins=[{"type": "left", "table_id": "t2",
                "on": {"base_column_id": "c1", "joined_column_id": "d1"}}],
    )
    row = out["rows"][0]
    # "Email" collides across both tables → table-name-prefixed; others bare.
    assert row == {
        "Connections.Email": "a@b.com",
        "Connection Removed": True,
        "People.Email": "a@b.com",
        "Title": "VP",
    }


def test_delete_table_rows_requires_confirm():
    with pytest.raises(ValueError):
        tools_tables.delete_table_rows("t1", [1, 2])


def test_delete_table_rows_reports_deleted(monkeypatch):
    monkeypatch.setattr(
        tables_api, "bulk_delete_rows",
        lambda table_id, row_ids: {"deleted_row_ids": [1, 2], "table": {"row_count": 3}},
    )
    out = tools_tables.delete_table_rows("t1", [1, 2, 99], confirm=True)
    assert out["deleted"] == 2 and out["deleted_row_ids"] == [1, 2]
