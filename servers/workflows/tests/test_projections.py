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
