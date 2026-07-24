"""Tests for the one-off data tools (tools_data) — upstream MCP federation
(blocked-result surfacing, unreachable-server messaging) and save_to_table
(target resolution, derived columns, per-row errors). The upstream MCP client
session is faked; no network."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from nrev_workflows_mcp import tables_api, tools_data


# ── fakes for the upstream MCP session ────────────────────────────────────────


class _FakeStream:
    """Stand-in for streamablehttp_client: an async CM yielding (read, write, _)."""

    raise_on_enter: Exception | None = None
    last_kwargs: dict | None = None

    def __init__(self, url, **kwargs):
        type(self).last_kwargs = {"url": url, **kwargs}

    async def __aenter__(self):
        if type(self).raise_on_enter is not None:
            raise type(self).raise_on_enter
        return (None, None, None)

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Stand-in for mcp.ClientSession recording call_tool invocations."""

    call_result = None
    list_result = None
    calls: list = []

    def __init__(self, read, write):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, name, arguments):
        type(self).calls.append((name, arguments))
        return type(self).call_result

    async def list_tools(self):
        return type(self).list_result


@pytest.fixture
def upstream(monkeypatch):
    _FakeStream.raise_on_enter = None
    _FakeSession.call_result = None
    _FakeSession.list_result = None
    _FakeSession.calls = []
    monkeypatch.setattr(tools_data, "streamablehttp_client", _FakeStream)
    monkeypatch.setattr(tools_data, "ClientSession", _FakeSession)
    monkeypatch.setattr(tools_data.auth, "get_jwt", lambda: "tok")
    monkeypatch.setattr(tools_data.auth, "force_refresh", lambda: None)
    return _FakeSession


def _text_result(payload, is_error=False):
    return SimpleNamespace(
        content=[SimpleNamespace(text=json.dumps(payload))],
        structuredContent=None,
        isError=is_error,
    )


# ── run_data_tool ─────────────────────────────────────────────────────────────


def test_run_data_tool_surfaces_blocked_estimate(upstream):
    upstream.call_result = _text_result(
        {"status": "blocked", "estimate": {"credits": 42, "rows": 10}, "reason": "spend gate"}
    )
    out = asyncio.run(tools_data.run_data_tool("linkedin_scraping__get_person_profile", {"url": "x"}))
    assert out["status"] == "blocked"
    assert out["estimate"] == {"credits": 42, "rows": 10}
    assert "confirm=true" in out["next_step"]
    # confirm=false forwarded upstream — the gate is server-side
    name, args = upstream.calls[0]
    assert name == "linkedin_scraping__get_person_profile"
    assert args == {"settings": {"url": "x"}, "confirm": False}


def test_run_data_tool_passes_confirm_through_and_parses_json(upstream):
    upstream.call_result = _text_result({"records": [{"name": "Ada"}], "credits_charged": 1})
    out = asyncio.run(tools_data.run_data_tool("t", {"a": 1}, confirm=True))
    assert out["status"] == "ok"
    assert out["result"] == {"records": [{"name": "Ada"}], "credits_charged": 1}
    assert upstream.calls[0][1]["confirm"] is True


def test_run_data_tool_prefers_structured_content(upstream):
    upstream.call_result = SimpleNamespace(
        content=[], structuredContent={"records": []}, isError=False
    )
    out = asyncio.run(tools_data.run_data_tool("t", {}, confirm=True))
    assert out["result"] == {"records": []}


def test_run_data_tool_returns_structured_error_for_unrecognized_upstream_error(upstream):
    upstream.call_result = _text_result("node exploded", is_error=True)
    out = asyncio.run(tools_data.run_data_tool("t", {}))
    assert out["status"] == "error"
    assert out["error_class"] == "UNKNOWN"
    assert "node exploded" in out["message"]
    assert out["details"] is None


def test_run_data_tool_classifies_leaked_jsonschema_error_as_invalid_input(upstream):
    # The exact leak seen in production: a raw jsonschema.ValidationError str()
    # crossing the MCP boundary instead of a structured error_class.
    upstream.call_result = _text_result(
        "Input validation error: [{'field_name': 'domain', 'field_value': 'stripe.com'}] "
        "is not of type 'object'",
        is_error=True,
    )
    out = asyncio.run(tools_data.run_data_tool("company_data__get_company_news", {}))
    assert out["status"] == "error"
    assert out["error_class"] == "INVALID_INPUT"


def test_run_data_tool_passes_through_structured_upstream_error(upstream):
    upstream.call_result = _text_result(
        {"error_class": "CREDITS_EXHAUSTED", "message": "tenant out of credits", "details": {"balance": 0}},
        is_error=True,
    )
    out = asyncio.run(tools_data.run_data_tool("t", {}))
    assert out == {
        "status": "error",
        "tool_name": "t",
        "error_class": "CREDITS_EXHAUSTED",
        "message": "tenant out of credits",
        "details": {"balance": 0},
    }


def test_unreachable_upstream_is_actionable(upstream):
    _FakeStream.raise_on_enter = ConnectionError("connection refused")
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(tools_data.list_data_tools())
    assert "not reachable" in str(exc.value)
    assert "workflow_studio /mcp" in str(exc.value)


def test_upstream_401_surfaces_sign_in_guidance(upstream):
    _FakeStream.raise_on_enter = RuntimeError("HTTP 401 Unauthorized")
    with pytest.raises(tools_data.auth.AuthError) as exc:
        asyncio.run(tools_data.run_data_tool("t", {}))
    assert "auth_login" in str(exc.value) or "auth login" in str(exc.value)


def test_list_data_tools_compacts_upstream_listing(upstream):
    upstream.list_result = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="company_data__get_company_signals",
                description="Fetch hiring/funding signals for a company.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "settings": {
                            "type": "object",
                            "properties": {
                                "domain": {"type": "string", "description": "Company domain"},
                                "signals": {"type": "array"},
                            },
                            "required": ["domain"],
                        },
                        "confirm": {"type": "boolean"},
                    },
                },
            )
        ]
    )
    out = asyncio.run(tools_data.list_data_tools())
    tool = out["data_tools"][0]
    assert tool["name"] == "company_data__get_company_signals"
    fields = {f["name"]: f for f in tool["settings_fields"]}
    assert fields["domain"]["required"] is True
    assert fields["signals"]["type"] == "array"
    assert "run_data_tool" in out["note"]


def test_list_data_tools_recurses_into_group_envelope_fields(upstream):
    # company_data__get_company_news-shaped tool: a `company_details` field
    # that is itself an object wrapping the real required key (`domain`) — the
    # flat hint used to report only {"name": "company_details", "type":
    # "object"} and hide that nested requirement entirely.
    upstream.list_result = SimpleNamespace(
        tools=[
            SimpleNamespace(
                name="company_data__get_company_news",
                description="Fetch recent news for a company.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "settings": {
                            "type": "object",
                            "properties": {
                                "company_details": {
                                    "type": "object",
                                    "properties": {
                                        "domain": {"type": "string", "description": "Company domain"},
                                    },
                                    "required": ["domain"],
                                },
                            },
                            "required": ["company_details"],
                        },
                        "confirm": {"type": "boolean"},
                    },
                },
            )
        ]
    )
    out = asyncio.run(tools_data.list_data_tools())
    fields = {f["name"]: f for f in out["data_tools"][0]["settings_fields"]}
    group = fields["company_details"]
    assert group["type"] == "object"
    assert group["required"] is True
    nested = {f["name"]: f for f in group["fields"]}
    assert nested["domain"]["type"] == "string"
    assert nested["domain"]["required"] is True


# ── save_to_table ─────────────────────────────────────────────────────────────

_TABLE = {
    "id": "t1",
    "name": "Prospects",
    "columns": [
        {"id": "c1", "name": "email", "type": "text"},
        {"id": "c2", "name": "profile", "type": "json"},
    ],
}


def test_save_to_table_creates_table_and_inserts(monkeypatch):
    created, added = {}, []
    monkeypatch.setattr(tools_data.tenant, "assert_pinned_active", lambda op: None)
    monkeypatch.setattr(tables_api, "list_tables", lambda **k: {"data": []})
    monkeypatch.setattr(
        tables_api, "create_table",
        lambda name, columns: created.update(name=name, columns=columns) or _TABLE,
    )
    monkeypatch.setattr(tables_api, "get_table", lambda table_id: _TABLE)
    monkeypatch.setattr(tables_api, "add_row", lambda table_id, values: added.append(values) or {})

    rows = [
        {"email": "a@b.com", "profile": {"title": "VP"}},
        {"email": 42, "profile": [1, 2]},  # non-str scalar → stringified for text col
    ]
    out = tools_data.save_to_table(rows, table_name="Prospects")

    assert created["name"] == "Prospects"
    assert created["columns"] == [
        {"name": "email", "type": "text"},
        {"name": "profile", "type": "json"},  # nested dict/list → json column
    ]
    assert out == {
        "table_id": "t1", "created": True, "inserted": 2, "failed": 0,
        "results": [{"row": 0, "ok": True}, {"row": 1, "ok": True}],
    }
    # values keyed by column id; nested structures JSON-serialized
    assert added[0] == {"c1": "a@b.com", "c2": '{"title": "VP"}'}
    assert added[1] == {"c1": "42", "c2": "[1, 2]"}


def test_save_to_table_finds_existing_table_by_name(monkeypatch):
    monkeypatch.setattr(tables_api, "list_tables", lambda **k: {"data": [_TABLE]})
    monkeypatch.setattr(tables_api, "get_table", lambda table_id: _TABLE)
    added = []
    monkeypatch.setattr(tables_api, "add_row", lambda table_id, values: added.append(values) or {})
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not create"))
    monkeypatch.setattr(tables_api, "create_table", boom)

    out = tools_data.save_to_table([{"email": "x@y.z"}], table_name="prospects")  # case-insensitive
    assert out["created"] is False
    assert out["inserted"] == 1
    assert added == [{"c1": "x@y.z"}]


def test_save_to_table_unknown_column_is_a_per_row_error(monkeypatch):
    monkeypatch.setattr(tables_api, "get_table", lambda table_id: _TABLE)
    monkeypatch.setattr(
        tables_api, "add_row",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("row with unknown column must not insert")),
    )
    out = tools_data.save_to_table([{"email": "a@b.com", "nope": 1}], table_id="t1")
    assert out["inserted"] == 0 and out["failed"] == 1
    err = out["results"][0]["error"]
    assert "nope" in err and "email" in err  # names the bad key and the available columns


def test_save_to_table_requires_a_target():
    with pytest.raises(ValueError):
        tools_data.save_to_table([{"a": 1}])


def test_save_to_table_no_create_when_disallowed(monkeypatch):
    monkeypatch.setattr(tables_api, "list_tables", lambda **k: {"data": []})
    with pytest.raises(ValueError) as exc:
        tools_data.save_to_table([{"a": 1}], table_name="Missing", create_if_missing=False)
    assert "Missing" in str(exc.value)
