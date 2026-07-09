"""Lexical relevance ranking for natural-language node discovery.

`search_nodes` hits the platform's substring filter — "send a message on Slack"
won't surface a node literally named "Slack". `find_node` fetches a broad
catalog slice and ranks it here against the user's *intent* with token overlap,
fuzzy name similarity, and a small GTM synonym map. This is deliberately
dependency-free (no embedding model): it is "semantic-ish" lexical ranking, a
big recall win over raw substring at zero install cost. True vector search is a
future upgrade — keep this the same shape (intent in, scored items out) so the
swap is local.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

# Domain synonyms: each key expands to extra search tokens so an intent phrased
# in business terms matches a node named for its underlying app/mechanism.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "email": ("gmail", "mail", "inbox", "outreach"),
    "mail": ("gmail", "email"),
    "message": ("slack", "send", "notify", "dm"),
    "slack": ("message", "notify", "channel"),
    "notify": ("slack", "message", "alert"),
    "sheet": ("spreadsheet", "googlesheets", "google", "rows"),
    "spreadsheet": ("sheet", "googlesheets", "google"),
    "crm": ("hubspot", "salesforce", "attio", "contact", "deal"),
    "deal": ("crm", "hubspot", "salesforce", "opportunity"),
    "enrich": ("enrichment", "apollo", "rocketreach", "person", "company", "contact"),
    "person": ("people", "contact", "lead", "enrich"),
    "people": ("person", "contact", "lead"),
    "company": ("companies", "account", "domain", "organization"),
    "scrape": ("scraping", "crawl", "extract", "web"),
    "linkedin": ("social", "profile", "post", "connection"),
    "search": ("find", "google", "lookup", "query"),
    "transform": ("magic", "manipulate", "map", "join", "merge", "reshape"),
    "join": ("merge", "magic", "combine"),
    "merge": ("join", "magic", "combine"),
    "filter": ("condition", "branch", "criteria"),
    "schedule": ("scheduler", "cron", "trigger", "recurring"),
    "webhook": ("trigger", "http", "incoming"),
    "ai": ("llm", "gpt", "model", "prompt", "ask"),
    "table": ("nrev", "tables", "database", "store", "query"),
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def expand(tokens: list[str]) -> set[str]:
    """Token set augmented with domain synonyms."""
    out = set(tokens)
    for t in tokens:
        out.update(SYNONYMS.get(t, ()))
    return out


def _item_text(item: dict) -> str:
    return " ".join(
        str(item.get(k) or "")
        for k in ("name", "type_slug", "category", "description")
    )


def score(intent_tokens: set[str], item: dict) -> float:
    """Relevance of one slim catalog item to an expanded intent token set.

    Blend of: token-overlap recall (how many intent terms the item mentions),
    a substring bonus for whole intent words appearing in the name, and fuzzy
    similarity on the name to catch near-spellings. Higher is better; 0 means
    no lexical signal at all.
    """
    if not intent_tokens:
        return 0.0
    name = (item.get("name") or "").lower()
    item_tokens = set(tokenize(_item_text(item)))
    if not item_tokens:
        return 0.0

    overlap = len(intent_tokens & item_tokens) / len(intent_tokens)
    name_tokens = set(tokenize(name))
    name_hits = len(intent_tokens & name_tokens)
    name_bonus = 0.25 * name_hits
    fuzzy = SequenceMatcher(None, " ".join(sorted(intent_tokens)), name).ratio()

    return round(2.0 * overlap + name_bonus + 0.5 * fuzzy, 4)


def rank(intent: str, items: list[dict], limit: int = 10) -> list[dict]:
    """Return the top `limit` items by relevance to `intent`, each annotated
    with a `_score`. Items with zero lexical signal are dropped."""
    intent_tokens = expand(tokenize(intent))
    scored = []
    for it in items:
        s = score(intent_tokens, it)
        if s > 0:
            scored.append({**it, "_score": s})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[: max(1, int(limit))]
