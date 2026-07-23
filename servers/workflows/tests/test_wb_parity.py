"""Tests for the v1.0.0 workflow-builder-parity client methods and tools —
endpoint paths, params, and body shapes verified against the FE services /
backend routes. Monkeypatched at the transport boundary, no network."""
import json

import pytest

from nrev_workflows_mcp import api, tools_execution, tools_workflows


@pytest.fixture
def seen(monkeypatch):
    """Capture what api.py hands the shared transport."""
    calls = {}

    def fake(host, method, path, json_body=None, params=None, files=None):
        calls.update(host=host, method=method, path=path, json_body=json_body, params=params, files=files)
        return {}

    monkeypatch.setattr(api, "_request", fake)
    return calls


# ── stop / resume (C3 bug fix + resume) ───────────────────────────────────────


def test_stop_execution_uses_stop_path_not_abort(seen):
    api.stop_execution("wf1", "ex1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/executions/workflow/wf1/workflow-execution/ex1/stop"


def test_stop_node_execution_path(seen):
    api.stop_node_execution("wf1", "ex1", "ne1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/executions/workflow/wf1/workflow-execution/ex1/node-execution/ne1/stop"


def test_resume_execution_without_envelope_sends_empty_body(seen):
    api.resume_execution("wf1", "ex1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/workflows/wf1/execution/ex1/update-and-resume"
    assert seen["json_body"] == {}


def test_resume_execution_wraps_envelope_in_workflow_details(seen):
    api.resume_execution("wf1", "ex1", envelope={"name": "x", "blocks": []})
    assert seen["json_body"] == {"workflow_details": {"name": "x", "blocks": []}}


def test_stop_execution_tool_calls_new_client_method(monkeypatch):
    called = {}
    monkeypatch.setattr(
        tools_execution.api, "stop_execution",
        lambda wf, ex: called.update(wf=wf, ex=ex) or None,
    )
    out = tools_execution.stop_execution("wf1", "ex1")
    assert called == {"wf": "wf1", "ex": "ex1"}
    assert out["stopped"] is True


# ── global run history + stats ────────────────────────────────────────────────


def test_list_global_executions_params(seen):
    api.list_global_executions(limit=5, skip=10, search="acme")
    assert seen["method"] == "GET"
    assert seen["path"] == "/execution-logs"
    assert seen["params"] == {"skip": 10, "limit": 5, "search": "acme"}


def test_execution_stats_path_and_date_range(seen):
    api.execution_stats(date_range="last_week")
    assert seen["method"] == "GET"
    assert seen["path"] == "/execution-logs/stats"
    assert seen["params"] == {"dateRange": "last_week"}


def test_list_recent_executions_projects_compact_rows(monkeypatch):
    raw = {
        "data": [
            {
                "id": "ex1",
                "status": "completed",
                "startedAt": "2026-07-01T00:00:00Z",
                "endedAt": "2026-07-01T00:01:00Z",
                "createdAt": "2026-07-01T00:00:00Z",
                "duration": 60.0,
                "creditsUsed": 12,
                "nodeExecutionCount": 7,
                "workflowVersion": 3,
                "workflowName": "Lead enrich",
                "workflowId": "wf1",
            }
        ],
        "meta": {"total_entries": 1, "skip": 0, "limit": 20},
    }
    monkeypatch.setattr(tools_execution.api, "list_global_executions", lambda **k: raw)
    out = tools_execution.list_recent_executions()
    row = out["executions"][0]
    assert row["execution_id"] == "ex1"
    assert row["workflow_id"] == "wf1"
    assert row["workflow_name"] == "Lead enrich"
    assert row["credits_used"] == 12
    assert "workflowVersion" not in row  # projected, not passed through
    assert out["meta"]["total_entries"] == 1


# ── listeners ─────────────────────────────────────────────────────────────────


def test_activate_listener_test_path_and_mode_param(seen):
    api.activate_listener_test("wf1", "n1", "full_workflow")
    assert seen["method"] == "POST"
    assert seen["path"] == "/listeners/workflow/wf1/node/n1/activate-test"
    assert seen["params"] == {"execution_mode": "full_workflow"}


def test_get_listener_latest_event_historical_param(seen):
    api.get_listener_latest_event("wf1", "n1", historical=True)
    assert seen["method"] == "GET"
    assert seen["path"] == "/listeners/workflow/wf1/node/n1/latest-event"
    assert seen["params"] == {"historical": "true"}


def test_deactivate_listener_path(seen):
    api.deactivate_listener("wf1", "n1")
    assert seen["method"] == "POST"
    assert seen["path"] == "/listeners/workflow/wf1/node/n1/deactivate"
    assert seen["json_body"] == {}


def test_activate_listener_tool_refuses_bad_mode():
    from nrev_workflows_mcp import tools_listeners

    with pytest.raises(ValueError):
        tools_listeners.activate_listener_test("wf1", "n1", execution_mode="warp_speed")


# ── connection OAuth URL ──────────────────────────────────────────────────────


def test_generate_connection_url_body_uses_camel_aliases(seen):
    api.generate_connection_url("app1", "https://ok", "https://err")
    assert seen["method"] == "POST"
    assert seen["path"] == "/connections/app1/url"
    assert seen["json_body"] == {"successRedirectUri": "https://ok", "errorRedirectUri": "https://err"}


# ── export / import / duplicate ───────────────────────────────────────────────


def test_download_workflow_json_is_a_get(seen):
    api.download_workflow_json("wf1")
    assert seen["method"] == "GET"
    assert seen["path"] == "/workflows/wf1/download-json"


def test_upload_workflow_json_is_multipart_upload_file(seen):
    api.upload_workflow_json("x.json", b"{}")
    assert seen["method"] == "POST"
    assert seen["path"] == "/workflows/upload-json"
    assert seen["json_body"] is None  # multipart, not JSON
    assert seen["files"] == {"upload_file": ("x.json", b"{}", "application/json")}


def test_duplicate_workflow_tool_derives_copy_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(tools_workflows.api, "get_workflow", lambda wf: {"id": wf, "name": "My Flow"})
    monkeypatch.setattr(
        tools_workflows.api, "duplicate_workflow",
        lambda wf, name: captured.update(name=name) or {"id": "wf2", "name": name, "blocks": []},
    )
    out = tools_workflows.duplicate_workflow("wf1")
    assert captured["name"] == "My Flow (copy)"
    assert out["id"] == "wf2"


def test_export_workflow_writes_json_file(monkeypatch, tmp_path):
    payload = {"name": "Flow X", "blocks": [{"id": "b1"}], "variables": [{"id": "v1"}]}
    monkeypatch.setattr(tools_workflows.api, "download_workflow_json", lambda wf: payload)
    target = tmp_path / "flow.json"
    out = tools_workflows.export_workflow("wf1", target_path=str(target))
    assert out["path"] == str(target)
    assert out["workflow_name"] == "Flow X"
    assert out["node_count"] == 1
    assert json.loads(target.read_text()) == payload


def test_export_workflow_refuses_overwrite(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_workflows.api, "download_workflow_json", lambda wf: {})
    target = tmp_path / "flow.json"
    target.write_text("{}")
    with pytest.raises(ValueError):
        tools_workflows.export_workflow("wf1", target_path=str(target))


def test_import_workflow_uploads_local_file(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(tools_workflows.tenant, "assert_pinned_active", lambda op: None)
    monkeypatch.setattr(
        tools_workflows.api, "upload_workflow_json",
        lambda filename, content: captured.update(filename=filename, content=content)
        or {"id": "wf9", "name": "Imported", "blocks": []},
    )
    src = tmp_path / "export.json"
    src.write_text('{"name": "Imported"}')
    out = tools_workflows.import_workflow(str(src))
    assert captured["filename"] == "export.json"
    assert captured["content"] == b'{"name": "Imported"}'
    assert out["id"] == "wf9"


def test_import_workflow_rejects_non_json(monkeypatch, tmp_path):
    monkeypatch.setattr(tools_workflows.tenant, "assert_pinned_active", lambda op: None)
    src = tmp_path / "notjson.json"
    src.write_text("not json at all {")
    with pytest.raises(ValueError):
        tools_workflows.import_workflow(str(src))
