"""Tests for the new pure helpers: cost estimation, option-field selection,
and lexical node ranking. No network."""
from nrev_workflows_mcp import projections, ranking


# ── estimate_cost ─────────────────────────────────────────────────────────────


def test_estimate_cost_sums_per_node_times_rows():
    blocks = [
        {"id": "a", "variableName": "Load", "creditCostPerItem": 0},
        {"id": "b", "variableName": "Enrich", "creditCostPerItem": 1},
        {"id": "c", "variableName": "Score", "creditCostPerItem": 0.5},
    ]
    est = projections.estimate_cost(blocks, rows=100)
    assert est["rows_assumed"] == 100
    assert est["estimated_credits_max"] == 150.0
    assert len(est["per_node"]) == 3


def test_estimate_cost_drivers_sorted_desc_and_zero_excluded():
    blocks = [
        {"id": "a", "creditCostPerItem": 0},
        {"id": "b", "creditCostPerItem": 2},
        {"id": "c", "creditCostPerItem": 1},
    ]
    est = projections.estimate_cost(blocks, rows=10)
    drivers = est["cost_drivers"]
    assert [d["node_id"] for d in drivers] == ["b", "c"]  # zero-cost node dropped


def test_estimate_cost_tolerates_snake_case_and_bad_values():
    blocks = [
        {"id": "a", "credit_cost_per_item": 3},      # snake_case
        {"id": "b", "creditCostPerItem": "oops"},     # non-numeric → 0
        {"id": "c"},                                   # missing → 0
    ]
    est = projections.estimate_cost(blocks, rows=2)
    assert est["estimated_credits_max"] == 6.0


def test_estimate_cost_negative_rows_clamped_to_zero():
    est = projections.estimate_cost([{"id": "a", "creditCostPerItem": 5}], rows=-10)
    assert est["rows_assumed"] == 0
    assert est["estimated_credits_max"] == 0.0


# ── fields_needing_options ──────────────────────────────────────────────────────


def test_fields_needing_options_picks_dynamic_only():
    detail = {
        "settings_fields": [
            {"name": "model", "dynamic_options_via": "get_field_options"},
            {"name": "prompt"},                       # static, no flag
            {"name": "channel", "dynamic_options_via": "get_field_options"},
            {"dynamic_options_via": "get_field_options"},  # no name → skipped
        ]
    }
    assert projections.fields_needing_options(detail) == ["model", "channel"]


def test_fields_needing_options_empty_detail():
    assert projections.fields_needing_options({}) == []


# ── ranking ─────────────────────────────────────────────────────────────────────


def _catalog():
    return [
        {"name": "Slack Send Message", "category": "messaging", "type_slug": "slack_send", "description": "Post a message to a Slack channel"},
        {"name": "Gmail Send Email", "category": "messaging", "type_slug": "gmail_send", "description": "Send an email via Gmail"},
        {"name": "Enrich Company", "category": "data", "type_slug": "enrich_company", "description": "Firmographic enrichment for a company domain"},
        {"name": "Magic Node", "category": "transform", "type_slug": "magic", "description": "AI transform: join, merge, reshape tabular data"},
        {"name": "Filter Rows", "category": "logic", "type_slug": "filter", "description": "Keep rows matching a condition"},
    ]


def test_rank_surfaces_synonym_match_for_slack():
    # "notify the team on Slack" — "notify" is a synonym hop to slack/message.
    top = ranking.rank("notify the team on Slack", _catalog(), limit=1)
    assert top[0]["name"] == "Slack Send Message"


def test_rank_matches_intent_without_literal_name_overlap():
    # "merge two lists" → Magic Node, whose name shares no words with the intent.
    top = ranking.rank("merge two lists together", _catalog(), limit=1)
    assert top[0]["name"] == "Magic Node"


def test_rank_email_intent_prefers_gmail():
    top = ranking.rank("send an email", _catalog(), limit=2)
    assert top[0]["name"] == "Gmail Send Email"


def test_rank_drops_zero_signal_and_respects_limit():
    out = ranking.rank("enrich a company", _catalog(), limit=10)
    assert out[0]["name"] == "Enrich Company"
    assert all(item["_score"] > 0 for item in out)


def test_rank_empty_intent_returns_nothing():
    assert ranking.rank("", _catalog(), limit=5) == []


def test_expand_adds_synonyms():
    expanded = ranking.expand(ranking.tokenize("email crm"))
    assert "gmail" in expanded and "hubspot" in expanded
