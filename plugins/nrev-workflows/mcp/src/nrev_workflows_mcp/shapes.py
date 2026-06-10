"""Workflow envelope construction and mutation.

Pure functions over the platform's workflow JSON ("envelope"). All knowledge
here was ported from the predecessor project's production-verified behavior:

  - The platform mixes camelCase and snake_case keys in the SAME objects
    (typeId / variableName / toBlocks / isTestMode vs settings_field_values /
    node_config_error). Faithful reproduction matters; don't normalize.
  - Every settings entry needs the full `sf` envelope — missing keys are
    silently accepted and then misbehave in the UI.
  - Edge IDs are `{source}-{src_handle}-{target}-{tgt_handle}`.
  - isTrigger marks a START NODE (≥1 required, several allowed — one per
    swimlane). isListener marks THE automation trigger (max one per workflow).
  - Almost every node is single-input; multi-input is the Magic Node
    (df1..df5 fan-in) and the legacy Merge block only.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Optional

# Stable platform UUIDs (from the predecessor's block_types registry).
MAGIC_NODE = "69f5628d-2c3b-4816-ac80-6825a1058ed5"
CUSTOM_CODE = "ae54c44f-60ee-47c4-91d7-eae7fa849133"
SCHEDULER = "68da2fb4-8295-4568-9415-c47de58e6224"

MAGIC_REFS_FIELD = "data_manipulation-magic_node-references"
MAGIC_FAN_IN_HANDLES = ("df1", "df2", "df3", "df4", "df5")
DEFAULT_HANDLE = "_default"


class OperationError(ValueError):
    """A workflow mutation was refused. The message explains why and what to
    do instead — surface it to the agent verbatim."""


def sf(name: str, value: Any, label: Optional[str] = None) -> dict:
    """Settings-field entry in the platform's expected envelope shape."""
    return {
        "field_name": name,
        "field_value": value,
        "fieldLabel": label,
        "error": None,
        "isUserInputInFormMandatory": False,
        "selectedInputTypeIndex": None,
        "isStale": False,
    }


def edge_id(source: str, src_handle: str, target: str, tgt_handle: str) -> str:
    return f"{source}-{src_handle}-{target}-{tgt_handle}"


def make_edge(source: str, target: str, src_handle: str, tgt_handle: str) -> dict:
    return {
        "edgeId": edge_id(source, src_handle, target, tgt_handle),
        "edge_source_handle_condition": src_handle,
        "edge_target_handle_condition": tgt_handle,
        "toBlockId": target,
    }


def blocks_of(wf: dict) -> list[dict]:
    return wf.get("blocks") or []


def block_by_id(wf: dict, node_id: str) -> Optional[dict]:
    return next((b for b in blocks_of(wf) if b.get("id") == node_id), None)


def block_name(block: dict) -> str:
    return block.get("variableName") or block.get("variable_name") or block.get("id", "?")


def settings_list(block: dict) -> list[dict]:
    return block.get("settings_field_values") or []


def get_setting(block: dict, field_name: str) -> Optional[dict]:
    return next((s for s in settings_list(block) if s.get("field_name") == field_name), None)


def set_setting(block: dict, field_name: str, value: Any, label: Optional[str] = None) -> None:
    """Upsert one setting, preserving the rest of an existing entry's envelope."""
    entry = get_setting(block, field_name)
    if entry is None:
        block.setdefault("settings_field_values", []).append(sf(field_name, value, label))
    else:
        entry["field_value"] = value
        if label is not None:
            entry["fieldLabel"] = label


def find_default_incoming(wf: dict, target_id: str) -> Optional[dict]:
    """First existing `_default` edge into target, as {source_id, edge}."""
    for b in blocks_of(wf):
        for e in b.get("toBlocks") or []:
            if e.get("toBlockId") == target_id and e.get("edge_target_handle_condition") == DEFAULT_HANDLE:
                return {"source_id": b.get("id"), "edge": e}
    return None


def all_edge_ids(wf: dict) -> set[str]:
    ids: set[str] = set()
    for b in blocks_of(wf):
        for e in b.get("toBlocks") or []:
            ids.add(
                e.get("edgeId")
                or edge_id(
                    b.get("id", ""),
                    e.get("edge_source_handle_condition", DEFAULT_HANDLE),
                    e.get("toBlockId", ""),
                    e.get("edge_target_handle_condition", DEFAULT_HANDLE),
                )
            )
    return ids


# ── Magic Node references (list of edge IDs, sometimes JSON-string-encoded) ──


def read_magic_references(block: dict) -> tuple[list[str], bool]:
    """Returns (references, was_json_string)."""
    entry = get_setting(block, MAGIC_REFS_FIELD)
    if entry is None:
        return [], False
    value = entry.get("field_value")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return (parsed if isinstance(parsed, list) else []), True
        except Exception:
            return [], True
    if isinstance(value, list):
        return list(value), False
    return [], False


def write_magic_references(block: dict, refs: list[str], as_json_string: bool) -> None:
    set_setting(block, MAGIC_REFS_FIELD, json.dumps(refs) if as_json_string else refs)


# ── Block construction ───────────────────────────────────────────────────────


def new_block(
    type_id: str,
    name: str,
    *,
    value_slug: Optional[str] = None,
    description: str = "",
    position: Optional[dict] = None,
    is_trigger: bool = False,
    is_listener: bool = False,
    test_mode: bool = False,
    settings: Optional[dict] = None,
    labels: Optional[dict] = None,
    node_id: Optional[str] = None,
) -> dict:
    settings = settings or {}
    labels = labels or {}
    return {
        "id": node_id or str(uuid.uuid4()),
        "typeId": type_id,
        "value": value_slug,
        "variableName": name,
        "description": description,
        "position": position or {"x": 100, "y": -100},
        "isTrigger": is_trigger,
        "isListener": is_listener,
        "isTestMode": test_mode,
        "isOrphan": False,
        "toBlocks": [],
        "settings_field_values": [sf(k, v, labels.get(k)) for k, v in settings.items()],
        "inputs": [],
        "outputs": [],
        "creditCostPerItem": 0,
        "node_config_error": None,
    }


# ── Operation engine ─────────────────────────────────────────────────────────
# apply_operations mutates a fetched workflow envelope in memory; the caller
# persists it afterwards (full PUT when structural, per-node PUTs otherwise —
# the predecessor hit 413s PUTting big workflows for edge-only changes).

NodeDefLookup = Callable[[str], dict]
"""callable(type_id) -> catalog info dict; must contain `is_trigger` and
`is_listener` booleans, and ideally `value` (slug) and `node_type_name`."""


def apply_operations(wf: dict, operations: list[dict], lookup: NodeDefLookup) -> dict:
    """Apply a batch of mutations to a workflow envelope (in place).

    Returns {summary, warnings, touched_node_ids, structural, ref_map}.
    Raises OperationError with an actionable message when an operation is
    refused; nothing is persisted by this function either way.
    """
    summary: list[str] = []
    warnings: list[str] = []
    touched: set[str] = set()
    structural = False
    ref_map: dict[str, str] = {}

    def resolve_id(maybe_ref: str) -> str:
        return ref_map.get(maybe_ref, maybe_ref)

    for i, op in enumerate(operations):
        kind = op.get("op")
        try:
            if kind == "add_node":
                node_id = _op_add_node(wf, op, lookup, ref_map, summary, warnings, touched)
                structural = True
                touched.add(node_id)
            elif kind == "add_edge":
                _op_add_edge(
                    wf,
                    resolve_id(op["source"]),
                    resolve_id(op["target"]),
                    op.get("source_handle", DEFAULT_HANDLE),
                    op.get("target_handle", DEFAULT_HANDLE),
                    bool(op.get("allow_multi_input", False)),
                    summary,
                    warnings,
                    touched,
                )
            elif kind == "remove_edge":
                _op_remove_edge(wf, resolve_id(op["source"]), resolve_id(op["target"]), op, summary, touched)
            elif kind == "remove_node":
                _op_remove_node(wf, resolve_id(op["node_id"]), summary, warnings, touched)
                structural = True
            elif kind == "rename_node":
                node = _require_block(wf, resolve_id(op["node_id"]))
                node["variableName"] = op["name"]
                touched.add(node["id"])
                summary.append(f"renamed node {node['id']} to {op['name']!r}")
            elif kind == "rename_workflow":
                wf["name"] = op["name"]
                structural = True
                summary.append(f"renamed workflow to {op['name']!r}")
            elif kind == "set_test_mode":
                _op_set_test_mode(wf, op, resolve_id, summary, touched)
            else:
                raise OperationError(
                    f"unknown op {kind!r} — supported: add_node, add_edge, remove_edge, "
                    f"remove_node, rename_node, rename_workflow, set_test_mode"
                )
        except KeyError as exc:
            raise OperationError(f"operation {i} ({kind}): missing required key {exc}") from exc

    return {
        "summary": summary,
        "warnings": warnings,
        "touched_node_ids": touched,
        "structural": structural,
        "ref_map": ref_map,
    }


def _require_block(wf: dict, node_id: str) -> dict:
    block = block_by_id(wf, node_id)
    if block is None:
        raise OperationError(f"node {node_id} not found in workflow {wf.get('id')}")
    return block


def _op_add_node(
    wf: dict,
    op: dict,
    lookup: NodeDefLookup,
    ref_map: dict,
    summary: list,
    warnings: list,
    touched: set,
) -> str:
    type_id = op["type_id"]
    name = op["name"]
    parents = [ref_map.get(p, p) for p in (op.get("parents") or [])]
    settings = op.get("settings") or {}
    info = lookup(type_id) or {}
    is_magic = type_id == MAGIC_NODE

    if len(parents) > 1 and not is_magic and not op.get("allow_multi_input"):
        raise OperationError(
            f"refusing to wire {len(parents)} parents into a single-input node "
            f"({name!r}). Use a Magic Node (type_id {MAGIC_NODE}, fan-in handles "
            f"df1..df5) to join or merge multiple data streams."
        )
    if is_magic and len(parents) > len(MAGIC_FAN_IN_HANDLES):
        raise OperationError(f"Magic Node supports at most {len(MAGIC_FAN_IN_HANDLES)} inputs")

    for p in parents:
        _require_block(wf, p)

    # Trigger/listener resolution — see module docstring for the vocabulary.
    catalog_trigger = bool(info.get("is_trigger"))
    catalog_listener = bool(info.get("is_listener"))
    is_trigger = op.get("is_trigger")
    is_listener = op.get("is_listener")

    if parents:
        resolved_trigger = bool(is_trigger) if is_trigger is not None else False
        resolved_listener = bool(is_listener) if is_listener is not None else False
    else:
        if not catalog_trigger and not op.get("force_root"):
            raise OperationError(
                f"node type {type_id!r} ({name!r}) cannot be a workflow start node — "
                f"the catalog marks it is_trigger=False (an action that needs a parent). "
                f"Attach a data source as the root and wire this downstream, or pass "
                f"force_root=true for a catalog edge case."
            )
        resolved_trigger = bool(is_trigger) if is_trigger is not None else True
        resolved_listener = bool(is_listener) if is_listener is not None else catalog_listener

        existing_listener = next((b for b in blocks_of(wf) if b.get("isListener")), None)
        if existing_listener is not None and resolved_listener:
            if op.get("force_demote_listener"):
                resolved_listener = False
                warnings.append(
                    f"new root {name!r} demoted to non-listener — workflow already has "
                    f"listener {block_name(existing_listener)!r}"
                )
            else:
                raise OperationError(
                    f"workflow already has a listener ({block_name(existing_listener)!r}) and "
                    f"the platform allows only one. Pass is_listener=false (start node, not "
                    f"automation trigger) or force_demote_listener=true."
                )
        if type_id == SCHEDULER and is_listener is False:
            raise OperationError(
                "Scheduler with is_listener=false is a footgun — a start node that never "
                "fires. For one-off runs use a real data source as the root; for cron "
                "automation leave the Scheduler as listener."
            )

    # Position: right of the rightmost parent, vertically centered between them.
    position = op.get("position")
    if position is None:
        if parents:
            pblocks = [block_by_id(wf, p) for p in parents]
            position = {
                "x": max((b.get("position") or {}).get("x", 0) for b in pblocks) + 400,
                "y": sum((b.get("position") or {}).get("y", 0) for b in pblocks) / len(pblocks),
            }
        else:
            position = {"x": 100, "y": -100}

    block = new_block(
        type_id,
        name,
        value_slug=info.get("value"),
        description=op.get("description", ""),
        position=position,
        is_trigger=resolved_trigger,
        is_listener=resolved_listener,
        test_mode=bool(op.get("test_mode", False)),
        settings=settings,
        labels=op.get("labels"),
    )
    wf.setdefault("blocks", []).append(block)
    if op.get("ref"):
        ref_map[op["ref"]] = block["id"]
    summary.append(f"added node {name!r} ({block['id']})")

    for idx, parent_id in enumerate(parents):
        tgt_handle = MAGIC_FAN_IN_HANDLES[idx] if is_magic else op.get("target_handle", DEFAULT_HANDLE)
        _op_add_edge(
            wf,
            parent_id,
            block["id"],
            op.get("source_handle", DEFAULT_HANDLE),
            tgt_handle,
            bool(op.get("allow_multi_input", False)),
            summary,
            warnings,
            touched,
        )
    return block["id"]


def _op_add_edge(
    wf: dict,
    source_id: str,
    target_id: str,
    src_handle: str,
    tgt_handle: str,
    allow_multi_input: bool,
    summary: list,
    warnings: list,
    touched: set,
) -> None:
    source = _require_block(wf, source_id)
    target = _require_block(wf, target_id)

    if tgt_handle == DEFAULT_HANDLE and not allow_multi_input:
        existing = find_default_incoming(wf, target_id)
        if existing and existing["source_id"] != source_id:
            raise OperationError(
                f"refusing a second `_default` edge into {block_name(target)!r} — it already "
                f"receives from {existing['source_id']}. Joins need a Magic Node (df1..df5 "
                f"handles); to replace the edge, remove_edge first; for the legacy Merge "
                f"block pass allow_multi_input=true."
            )

    edges = source.get("toBlocks") or []
    for e in edges:
        if (
            e.get("toBlockId") == target_id
            and e.get("edge_source_handle_condition") == src_handle
            and e.get("edge_target_handle_condition") == tgt_handle
        ):
            summary.append(f"edge {source_id} → {target_id} already existed")
            return

    edge = make_edge(source_id, target_id, src_handle, tgt_handle)
    edges.append(edge)
    source["toBlocks"] = edges
    touched.add(source_id)
    summary.append(f"wired {block_name(source)!r} → {block_name(target)!r} ({src_handle}→{tgt_handle})")

    # Target-side fix-ups the platform does NOT do automatically:
    if target.get("isOrphan"):
        target["isOrphan"] = False
        touched.add(target_id)
    if not target.get("inputs"):
        # Skeleton mirroring the UI; the platform fills real file/columns at run.
        target["inputs"] = [
            {"node_id": source_id, "file": "", "columns": [], "handle_condition": src_handle}
        ]
        touched.add(target_id)
    if target.get("isTrigger"):
        target["isTrigger"] = False
        touched.add(target_id)
        warnings.append(
            f"{block_name(target)!r} was a start node; wiring an edge into it flipped "
            f"isTrigger=false so the workflow doesn't keep an unintended extra root"
        )
    if target.get("typeId") == MAGIC_NODE:
        refs, as_str = read_magic_references(target)
        if edge["edgeId"] not in refs:
            refs.append(edge["edgeId"])
            write_magic_references(target, refs, as_str)
            touched.add(target_id)
            summary.append(f"updated Magic Node references on {block_name(target)!r}")


def _op_remove_edge(wf: dict, source_id: str, target_id: str, op: dict, summary: list, touched: set) -> None:
    source = _require_block(wf, source_id)
    sh = op.get("source_handle")
    th = op.get("target_handle")
    before = source.get("toBlocks") or []
    removed = [
        e
        for e in before
        if e.get("toBlockId") == target_id
        and (sh is None or e.get("edge_source_handle_condition") == sh)
        and (th is None or e.get("edge_target_handle_condition") == th)
    ]
    if not removed:
        raise OperationError(f"no edge {source_id} → {target_id} found")
    source["toBlocks"] = [e for e in before if e not in removed]
    touched.add(source_id)

    target = block_by_id(wf, target_id)
    if target is not None and target.get("typeId") == MAGIC_NODE:
        refs, as_str = read_magic_references(target)
        removed_ids = {e.get("edgeId") for e in removed}
        kept = [r for r in refs if r not in removed_ids]
        if kept != refs:
            write_magic_references(target, kept, as_str)
            touched.add(target_id)
    summary.append(f"removed {len(removed)} edge(s) {source_id} → {target_id}")


def _op_remove_node(wf: dict, node_id: str, summary: list, warnings: list, touched: set) -> None:
    block = _require_block(wf, node_id)
    wf["blocks"] = [b for b in blocks_of(wf) if b.get("id") != node_id]
    for b in blocks_of(wf):
        edges = b.get("toBlocks") or []
        kept = [e for e in edges if e.get("toBlockId") != node_id]
        if len(kept) != len(edges):
            b["toBlocks"] = kept
            touched.add(b.get("id"))
        if b.get("typeId") == MAGIC_NODE:
            refs, as_str = read_magic_references(b)
            kept_refs = [r for r in refs if node_id not in r]
            if kept_refs != refs:
                write_magic_references(b, kept_refs, as_str)
                touched.add(b.get("id"))
                warnings.append(
                    f"Magic Node {block_name(b)!r} referenced removed node {node_id}; "
                    f"references pruned — re-check its prompt/inputs"
                )
    summary.append(f"removed node {block_name(block)!r} ({node_id})")


def _op_set_test_mode(wf: dict, op: dict, resolve_id, summary: list, touched: set) -> None:
    value = bool(op.get("value", True))
    if op.get("all"):
        for b in blocks_of(wf):
            b["isTestMode"] = value
            touched.add(b.get("id"))
        summary.append(f"set isTestMode={value} on all {len(blocks_of(wf))} nodes")
    else:
        node = _require_block(wf, resolve_id(op["node_id"]))
        node["isTestMode"] = value
        touched.add(node["id"])
        summary.append(f"set isTestMode={value} on {block_name(node)!r}")
