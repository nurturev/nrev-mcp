"""Tests for execution tools — row-level error scanning. Monkeypatched, no network."""
from nrev_workflows_mcp import tools_execution


def test_row_error_detects_error_columns():
    assert tools_execution._row_error({"error": '{"x":1}', "name": "a"}) == '{"x":1}'
    assert tools_execution._row_error({"error_1": "boom"}) == "boom"
    # empties / nulls are not errors
    assert tools_execution._row_error({"error": None}) is None
    assert tools_execution._row_error({"error": ""}) is None
    assert tools_execution._row_error({"error": "null"}) is None
    # non-error columns ignored
    assert tools_execution._row_error({"errors_summary": "x", "value": 1}) is None


def test_check_node_errors_flags_failed_rows(monkeypatch):
    page = {"data": [{"error": None, "x": 1}, {"error_1": "boom", "x": 2}]}
    monkeypatch.setattr(tools_execution.api, "get_node_preview", lambda *a, **k: page)
    out = tools_execution.check_node_errors("wf", "ex", node_id="n")
    assert out["rows_scanned"] == 2
    assert out["rows_with_errors"] == 1
    assert out["clean"] is False
    assert out["errors"][0] == {"row_index": 1, "error": "boom"}


def test_check_node_errors_clean_run(monkeypatch):
    monkeypatch.setattr(
        tools_execution.api, "get_node_preview",
        lambda *a, **k: {"data": [{"error": None}, {"error": ""}]},
    )
    out = tools_execution.check_node_errors("wf", "ex", node_id="n")
    assert out["clean"] is True and out["rows_with_errors"] == 0


def test_check_node_errors_uses_node_execution_endpoint_when_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        tools_execution.api, "get_node_execution_preview",
        lambda *a, **k: seen.update(called=True) or {"data": []},
    )
    monkeypatch.setattr(
        tools_execution.api, "get_node_preview",
        lambda *a, **k: seen.update(wrong=True) or {"data": []},
    )
    tools_execution.check_node_errors("wf", "ex", node_execution_id="ne-1")
    assert seen.get("called") and not seen.get("wrong")


def test_check_node_errors_requires_a_target():
    import pytest

    with pytest.raises(ValueError):
        tools_execution.check_node_errors("wf", "ex")
