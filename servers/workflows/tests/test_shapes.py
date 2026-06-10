"""Tests for the workflow mutation engine — pure functions, no network."""
import json

import pytest

from nrev_workflows_mcp import shapes
from nrev_workflows_mcp.shapes import OperationError, apply_operations


def lookup_factory(flags=None):
    flags = flags or {}

    def lookup(type_id):
        return flags.get(type_id, {"is_trigger": True, "is_listener": False, "value": "slug", "name": "X"})

    return lookup


def empty_wf(**kw):
    wf = {"id": "wf-1", "name": "Test", "blocks": []}
    wf.update(kw)
    return wf


TRIGGER_TYPE = "type-trigger"
ACTION_TYPE = "type-action"
LISTENER_TYPE = "type-listener"

FLAGS = {
    TRIGGER_TYPE: {"is_trigger": True, "is_listener": False, "value": "trigger.slug", "name": "Trig"},
    ACTION_TYPE: {"is_trigger": False, "is_listener": False, "value": "action.slug", "name": "Act"},
    LISTENER_TYPE: {"is_trigger": True, "is_listener": True, "value": "listener.slug", "name": "Lis"},
    shapes.MAGIC_NODE: {"is_trigger": False, "is_listener": False, "value": "magic.slug", "name": "Magic"},
}


def test_sf_envelope_has_all_required_keys():
    entry = shapes.sf("f", "v", "L")
    assert entry == {
        "field_name": "f",
        "field_value": "v",
        "fieldLabel": "L",
        "error": None,
        "isUserInputInFormMandatory": False,
        "selectedInputTypeIndex": None,
        "isStale": False,
    }


def test_edge_id_format():
    assert shapes.edge_id("a", "_default", "b", "df1") == "a-_default-b-df1"


def test_add_root_and_child_wires_edge_and_inputs():
    wf = empty_wf()
    result = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "Root", "parents": [], "ref": "r"},
            {"op": "add_node", "type_id": ACTION_TYPE, "name": "Child", "parents": ["r"], "ref": "c"},
        ],
        lookup_factory(FLAGS),
    )
    assert result["structural"]
    root_id, child_id = result["ref_map"]["r"], result["ref_map"]["c"]
    root = shapes.block_by_id(wf, root_id)
    child = shapes.block_by_id(wf, child_id)
    assert root["isTrigger"] is True
    assert child["isTrigger"] is False
    assert root["toBlocks"][0]["toBlockId"] == child_id
    assert root["toBlocks"][0]["edgeId"] == shapes.edge_id(root_id, "_default", child_id, "_default")
    assert child["inputs"][0]["node_id"] == root_id
    assert root["value"] == "trigger.slug"
    # child auto-positioned right of parent
    assert child["position"]["x"] == root["position"]["x"] + 400


def test_action_type_refused_as_root():
    with pytest.raises(OperationError, match="cannot be a workflow start node"):
        apply_operations(
            empty_wf(),
            [{"op": "add_node", "type_id": ACTION_TYPE, "name": "Bad", "parents": []}],
            lookup_factory(FLAGS),
        )


def test_second_listener_refused_then_demoted_with_force():
    wf = empty_wf()
    ops = [{"op": "add_node", "type_id": LISTENER_TYPE, "name": "L1", "parents": []}]
    apply_operations(wf, ops, lookup_factory(FLAGS))

    with pytest.raises(OperationError, match="already has a listener"):
        apply_operations(
            wf,
            [{"op": "add_node", "type_id": LISTENER_TYPE, "name": "L2", "parents": []}],
            lookup_factory(FLAGS),
        )

    result = apply_operations(
        wf,
        [{"op": "add_node", "type_id": LISTENER_TYPE, "name": "L2", "parents": [], "force_demote_listener": True}],
        lookup_factory(FLAGS),
    )
    assert any("demoted" in w for w in result["warnings"])
    listeners = [b for b in wf["blocks"] if b["isListener"]]
    assert len(listeners) == 1


def test_single_input_guard_refuses_second_default_edge():
    wf = empty_wf()
    result = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"},
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "B", "parents": [], "is_listener": False, "ref": "b"},
            {"op": "add_node", "type_id": ACTION_TYPE, "name": "C", "parents": ["a"], "ref": "c"},
        ],
        lookup_factory(FLAGS),
    )
    ids = result["ref_map"]
    with pytest.raises(OperationError, match="Magic Node"):
        apply_operations(
            wf,
            [{"op": "add_edge", "source": ids["b"], "target": ids["c"]}],
            lookup_factory(FLAGS),
        )


def test_multi_parent_refused_for_plain_node_allowed_for_magic():
    wf = empty_wf()
    base = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"},
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "B", "parents": [], "is_listener": False, "ref": "b"},
        ],
        lookup_factory(FLAGS),
    )
    ids = base["ref_map"]

    with pytest.raises(OperationError, match="single-input"):
        apply_operations(
            wf,
            [{"op": "add_node", "type_id": ACTION_TYPE, "name": "Join", "parents": [ids["a"], ids["b"]]}],
            lookup_factory(FLAGS),
        )

    result = apply_operations(
        wf,
        [{"op": "add_node", "type_id": shapes.MAGIC_NODE, "name": "Join", "parents": [ids["a"], ids["b"]], "ref": "m"}],
        lookup_factory(FLAGS),
    )
    magic = shapes.block_by_id(wf, result["ref_map"]["m"])
    incoming = [
        e for b in wf["blocks"] for e in (b.get("toBlocks") or []) if e["toBlockId"] == magic["id"]
    ]
    assert sorted(e["edge_target_handle_condition"] for e in incoming) == ["df1", "df2"]
    refs, _ = shapes.read_magic_references(magic)
    assert len(refs) == 2
    assert all(r in shapes.all_edge_ids(wf) for r in refs)


def test_magic_references_json_string_roundtrip():
    block = shapes.new_block(shapes.MAGIC_NODE, "M", settings={shapes.MAGIC_REFS_FIELD: json.dumps(["e1"])})
    refs, as_str = shapes.read_magic_references(block)
    assert refs == ["e1"] and as_str
    shapes.write_magic_references(block, ["e1", "e2"], as_str)
    entry = shapes.get_setting(block, shapes.MAGIC_REFS_FIELD)
    assert json.loads(entry["field_value"]) == ["e1", "e2"]


def test_wiring_into_start_node_flips_trigger_with_warning():
    wf = empty_wf()
    base = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"},
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "B", "parents": [], "is_listener": False, "ref": "b"},
        ],
        lookup_factory(FLAGS),
    )
    ids = base["ref_map"]
    result = apply_operations(
        wf,
        [{"op": "add_edge", "source": ids["a"], "target": ids["b"]}],
        lookup_factory(FLAGS),
    )
    assert shapes.block_by_id(wf, ids["b"])["isTrigger"] is False
    assert any("flipped isTrigger" in w for w in result["warnings"])


def test_remove_node_cleans_edges_and_magic_refs():
    wf = empty_wf()
    base = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"},
            {"op": "add_node", "type_id": shapes.MAGIC_NODE, "name": "M", "parents": ["a"], "ref": "m"},
        ],
        lookup_factory(FLAGS),
    )
    ids = base["ref_map"]
    result = apply_operations(wf, [{"op": "remove_node", "node_id": ids["a"]}], lookup_factory(FLAGS))
    assert shapes.block_by_id(wf, ids["a"]) is None
    magic = shapes.block_by_id(wf, ids["m"])
    refs, _ = shapes.read_magic_references(magic)
    assert refs == []
    assert any("references pruned" in w for w in result["warnings"])


def test_remove_edge_and_idempotent_add():
    wf = empty_wf()
    base = apply_operations(
        wf,
        [
            {"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"},
            {"op": "add_node", "type_id": ACTION_TYPE, "name": "B", "parents": ["a"], "ref": "b"},
        ],
        lookup_factory(FLAGS),
    )
    ids = base["ref_map"]
    dup = apply_operations(
        wf, [{"op": "add_edge", "source": ids["a"], "target": ids["b"]}], lookup_factory(FLAGS)
    )
    assert any("already existed" in s for s in dup["summary"])
    apply_operations(wf, [{"op": "remove_edge", "source": ids["a"], "target": ids["b"]}], lookup_factory(FLAGS))
    assert shapes.block_by_id(wf, ids["a"])["toBlocks"] == []
    with pytest.raises(OperationError, match="no edge"):
        apply_operations(wf, [{"op": "remove_edge", "source": ids["a"], "target": ids["b"]}], lookup_factory(FLAGS))


def test_rename_and_test_mode_ops():
    wf = empty_wf()
    base = apply_operations(
        wf,
        [{"op": "add_node", "type_id": TRIGGER_TYPE, "name": "A", "parents": [], "ref": "a"}],
        lookup_factory(FLAGS),
    )
    ids = base["ref_map"]
    result = apply_operations(
        wf,
        [
            {"op": "rename_node", "node_id": ids["a"], "name": "A2"},
            {"op": "rename_workflow", "name": "WF2"},
            {"op": "set_test_mode", "all": True, "value": True},
        ],
        lookup_factory(FLAGS),
    )
    assert wf["name"] == "WF2"
    assert shapes.block_by_id(wf, ids["a"])["variableName"] == "A2"
    assert shapes.block_by_id(wf, ids["a"])["isTestMode"] is True
    assert result["structural"]  # rename_workflow forces a full PUT


def test_unknown_op_rejected():
    with pytest.raises(OperationError, match="unknown op"):
        apply_operations(empty_wf(), [{"op": "explode"}], lookup_factory(FLAGS))


def test_scheduler_non_listener_refused():
    flags = dict(FLAGS)
    flags[shapes.SCHEDULER] = {"is_trigger": True, "is_listener": True, "value": "sched", "name": "Sched"}
    with pytest.raises(OperationError, match="footgun"):
        apply_operations(
            empty_wf(),
            [{"op": "add_node", "type_id": shapes.SCHEDULER, "name": "S", "parents": [], "is_listener": False}],
            lookup_factory(flags),
        )
