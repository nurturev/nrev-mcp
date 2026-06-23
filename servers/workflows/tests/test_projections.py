"""Tests for response projections — pure functions, no network."""
from nrev_workflows_mcp import projections, shapes


def test_slim_workflow_strips_settings():
    wf = {
        "id": "wf-1",
        "name": "Test",
        "workflowConfigError": None,
        "isRunable": True,
        "blocks": [
            {
                "id": "n1",
                "variableName": "Root",
                "typeId": "t1",
                "isTrigger": True,
                "isListener": True,
                "isTestMode": False,
                "settings_field_values": [{"field_name": "big", "field_value": "x" * 5000}],
                "toBlocks": [
                    {
                        "toBlockId": "n2",
                        "edge_source_handle_condition": "_default",
                        "edge_target_handle_condition": "_default",
                    }
                ],
            }
        ],
    }
    slim = projections.slim_workflow(wf)
    assert slim["node_count"] == 1
    node = slim["nodes"][0]
    assert "settings_field_values" not in node
    assert node["edges_out"] == [{"to": "n2", "src_handle": "_default", "tgt_handle": "_default"}]
    assert "x" * 100 not in str(slim)


def test_scan_validation_flags_errors_and_bad_magic_refs():
    wf = {
        "id": "wf-1",
        "workflowConfigError": "broken",
        "blocks": [
            {"id": "n1", "variableName": "A", "node_config_error": "missing field", "toBlocks": []},
            {
                "id": "n2",
                "variableName": "M",
                "typeId": shapes.MAGIC_NODE,
                "toBlocks": [],
                "settings_field_values": [
                    {"field_name": shapes.MAGIC_REFS_FIELD, "field_value": ["not-a-real-edge"]}
                ],
            },
        ],
    }
    report = projections.scan_validation(wf)
    assert report["valid"] is False
    assert report["workflow_config_error"] == "broken"
    assert report["node_errors"][0]["node_id"] == "n1"
    assert report["magic_reference_warnings"][0]["reference"] == "not-a-real-edge"


def test_scan_validation_flags_unconfigured_downstream_node():
    # A non-trigger node with an incoming edge but zero settings is the silent
    # failure mode (e.g. an unconfigured Filter) — advisory, does not flip valid.
    wf = {
        "id": "wf-1",
        "blocks": [
            {
                "id": "root",
                "variableName": "Source",
                "isTrigger": True,
                "settings_field_values": [{"field_name": "q", "field_value": "x"}],
                "toBlocks": [{"toBlockId": "flt", "edge_target_handle_condition": "_default"}],
            },
            {"id": "flt", "variableName": "Filter", "settings_field_values": [], "toBlocks": []},
        ],
    }
    report = projections.scan_validation(wf)
    assert report["valid"] is True  # advisory only
    warned = report["unconfigured_warnings"]
    assert len(warned) == 1 and warned[0]["node_id"] == "flt"
    # the trigger root (has settings, no incoming edge) is not flagged
    assert all(w["node_id"] != "root" for w in warned)


def test_scan_validation_does_not_flag_configured_or_root_nodes():
    wf = {
        "id": "wf-1",
        "blocks": [
            {"id": "root", "isTrigger": True, "settings_field_values": [], "toBlocks": [{"toBlockId": "n2"}]},
            {"id": "n2", "settings_field_values": [{"field_name": "x", "field_value": 1}], "toBlocks": []},
        ],
    }
    report = projections.scan_validation(wf)
    # root: no incoming edge → skipped; n2: has settings → skipped
    assert report["unconfigured_warnings"] == []


def test_slim_execution_flags_zero_row_nodes():
    raw = {
        "id": "exec-1",
        "status": "completed",
        "blockRuns": [
            {"id": "ne-1", "workflowBlockId": "filter", "workflowBlockName": "Keep fits",
             "status": "completed", "output": [{"file_info": {"rows_count": 0}}]},
            {"id": "ne-2", "workflowBlockId": "search", "workflowBlockName": "Search",
             "status": "completed", "output": [{"file_info": {"rows_count": 12}}]},
        ],
    }
    slim = projections.slim_execution(raw)
    assert [z["node_id"] for z in slim["zero_row_nodes"]] == ["filter"]
    assert slim["warnings"] and "0 rows" in slim["warnings"][0]


def test_slim_execution_no_warning_when_all_nodes_have_rows():
    raw = {
        "id": "exec-1",
        "status": "completed",
        "blockRuns": [
            {"id": "ne-1", "workflowBlockId": "a", "status": "completed",
             "output": [{"file_info": {"rows_count": 3}}]},
        ],
    }
    slim = projections.slim_execution(raw)
    assert "zero_row_nodes" not in slim and "warnings" not in slim


def test_slim_definition_detail_truncates_options():
    detail = projections.slim_definition_detail(
        {
            "node_definition_id": "d1",
            "name": "Pick",
            "settings": [
                {"name": "f1", "type": "select", "options": [{"v": i} for i in range(100)]},
                {"name": "f2", "type": "string", "inputTypes": [{"dataSource": {"endpoint": "/nodes/field-options"}}]},
            ],
        }
    )
    f1, f2 = detail["settings_fields"]
    assert len(f1["options"]) == 25 and f1["options_truncated"] == 100
    assert f2["dynamic_options_via"] == "get_field_options"


def test_extract_execution_id_handles_known_shapes():
    cases = [
        ({"execution": {"response": {"id": "e1"}}}, "e1"),
        ({"execution_id": "e2"}, "e2"),
        ({"data": {"id": "e3"}}, "e3"),
        ({"id": "e4"}, "e4"),
        ({"nothing": True}, None),
    ]
    for raw, expected in cases:
        assert projections.extract_execution_id(raw) == expected


def test_slim_execution_running_detection():
    running = projections.slim_execution({"id": "e1", "status": "RUNNING"})
    done = projections.slim_execution({"id": "e1", "status": "completed"})
    assert running["is_running"] is True
    assert done["is_running"] is False


def test_slim_execution_reads_block_runs():
    # The platform returns the per-node-RUN list under `blockRuns` (camelCase
    # aliases). Each run keeps its own node_execution_id; a node that ran twice
    # appears twice. row_count comes from output[].file_info.rows_count.
    raw = {
        "id": "exec-1",
        "status": "completed",
        "creditsUsed": 9440,
        "nodeExecutionCount": 131,
        "blockRuns": [
            {
                "id": "ne-1",
                "workflowBlockId": "node-A",
                "workflowBlockName": "Find Competitor Sales Reps",
                "status": "completed",
                "creditsUsed": 3,
                "output": [{"file_info": {"rows_count": 58}, "handle_condition": "_default"}],
            },
            {
                "id": "ne-2",
                "workflowBlockId": "node-A",
                "workflowBlockName": "Find Competitor Sales Reps",
                "status": "completed",
                "creditsUsed": 0,
                "output": [{"file_info": {"rows_count": 25}}, {"file_info": {"rows_count": 5}}],
            },
        ],
    }
    slim = projections.slim_execution(raw)
    assert slim["node_execution_count"] == 131
    assert slim["credits_used"] == 9440
    runs = slim["node_runs"]
    assert [r["node_execution_id"] for r in runs] == ["ne-1", "ne-2"]
    # Same node_id appears once per run (loop/fan-out), distinct run ids.
    assert {r["node_id"] for r in runs} == {"node-A"}
    assert runs[0]["row_count"] == 58
    assert runs[1]["row_count"] == 30  # summed across output handles
    assert runs[0]["node_name"] == "Find Competitor Sales Reps"
