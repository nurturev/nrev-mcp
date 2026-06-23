"""Tests for catalog discovery tools — get_field_options connection self-heal.
Monkeypatched, no network."""
import pytest

from nrev_workflows_mcp import tools_discovery

SLACK_SCHEMA = {
    "settings": [
        {"name": "pipedream-slack_v2-slack_v2_send_message-slack_connection_id", "type": "app_connection"},
        {"name": "pipedream-slack_v2-slack_v2_send_message-icon_emoji", "type": "string"},
    ]
}


def test_heal_connection_field_remaps_to_schema_name(monkeypatch):
    monkeypatch.setattr(tools_discovery.api, "get_node_definition", lambda nd: SLACK_SCHEMA)
    # caller supplied the connection under the wrong (constructed) field name
    settings = {"pipedream-slack_v2-send_message-connection": "conn-uuid"}
    exc = Exception("Field options error: No valid connection found in settings.")
    repaired = tools_discovery._heal_connection_field("nd-1", settings, exc)
    assert repaired == {"pipedream-slack_v2-slack_v2_send_message-slack_connection_id": "conn-uuid"}


def test_heal_connection_field_ignores_unrelated_errors():
    assert tools_discovery._heal_connection_field("nd-1", {"x-connection": "u"}, Exception("boom 500")) is None


def test_heal_connection_field_noop_when_name_already_correct(monkeypatch):
    monkeypatch.setattr(tools_discovery.api, "get_node_definition", lambda nd: SLACK_SCHEMA)
    settings = {"pipedream-slack_v2-slack_v2_send_message-slack_connection_id": "conn-uuid"}
    assert tools_discovery._heal_connection_field("nd-1", settings, Exception("No valid connection found")) is None


def test_get_field_options_retries_after_healing(monkeypatch):
    monkeypatch.setattr(tools_discovery.api, "get_node_definition", lambda nd: SLACK_SCHEMA)
    calls = []

    def fake_field_options(node_id, ndid, field_name, settings_list, search=None):
        calls.append(settings_list)
        if len(calls) == 1:
            raise Exception("Field options error: No valid connection found in settings.")
        return {"data": [{"label": "general", "value": "C1"}]}

    monkeypatch.setattr(tools_discovery.api, "field_options", fake_field_options)
    out = tools_discovery.get_field_options(
        "nd-1",
        "pipedream-slack_v2-slack_v2_send_message-icon_emoji",
        settings={"pipedream-slack_v2-send_message-connection": "conn-uuid"},
    )
    assert out["data"][0]["value"] == "C1"
    assert len(calls) == 2  # failed once, retried once
    remapped = {e["field_name"]: e["field_value"] for e in calls[1]}
    assert remapped == {"pipedream-slack_v2-slack_v2_send_message-slack_connection_id": "conn-uuid"}


def test_get_field_options_happy_path_does_not_refetch_schema(monkeypatch):
    def boom(nd):
        raise AssertionError("schema should not be fetched on the happy path")

    monkeypatch.setattr(tools_discovery.api, "get_node_definition", boom)
    monkeypatch.setattr(tools_discovery.api, "field_options", lambda *a, **k: {"data": ["ok"]})
    assert tools_discovery.get_field_options("nd-1", "f", settings={"a": "b"}) == {"data": ["ok"]}


def test_get_field_options_reraises_when_unhealable(monkeypatch):
    monkeypatch.setattr(tools_discovery.api, "get_node_definition", lambda nd: {"settings": []})
    monkeypatch.setattr(
        tools_discovery.api,
        "field_options",
        lambda *a, **k: (_ for _ in ()).throw(Exception("No valid connection found in settings")),
    )
    with pytest.raises(Exception, match="No valid connection"):
        tools_discovery.get_field_options("nd-1", "f", settings={"x-connection": "u"})
