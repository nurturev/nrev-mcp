"""Tests for oauth.py: the OAuth 2.1 authorization server provider that lets
Cowork/Claude connect to the hosted transport, and the webapp-callback route
that receives the handoff from the nRev web app's existing relay login.
Session storage is faked in-memory — no real Redis, no real network, no real
web app.
"""
import asyncio
import time
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import AuthorizationParams, TokenError
from mcp.shared.auth import OAuthClientInformationFull

from nrev_workflows_mcp import config, oauth, session_store


@pytest.fixture
def fake_store(monkeypatch):
    """Dict-backed fake for every session_store function oauth.py touches."""
    state = {"corr": {}, "codes": {}, "sessions": {}, "at": {}, "rt": {}, "clients": {}}

    monkeypatch.setattr(session_store, "save_correlation", lambda n, d: state["corr"].__setitem__(n, d))
    monkeypatch.setattr(session_store, "pop_correlation", lambda n: state["corr"].pop(n, None))
    monkeypatch.setattr(session_store, "save_auth_code", lambda c, d: state["codes"].__setitem__(c, d))
    monkeypatch.setattr(session_store, "load_auth_code", lambda c: state["codes"].get(c))
    monkeypatch.setattr(session_store, "delete_auth_code", lambda c: state["codes"].pop(c, None))
    monkeypatch.setattr(session_store, "save_session", lambda sid, d: state["sessions"].__setitem__(sid, d))
    monkeypatch.setattr(session_store, "load_session", lambda sid: state["sessions"].get(sid))
    monkeypatch.setattr(session_store, "save_access_token", lambda t, d, ttl=None: state["at"].__setitem__(t, d))
    monkeypatch.setattr(session_store, "load_access_token", lambda t: state["at"].get(t))
    monkeypatch.setattr(session_store, "revoke_access_token", lambda t: state["at"].pop(t, None))
    monkeypatch.setattr(session_store, "save_refresh_token", lambda t, d, ttl=None: state["rt"].__setitem__(t, d))
    monkeypatch.setattr(session_store, "load_refresh_token", lambda t: state["rt"].get(t))
    monkeypatch.setattr(session_store, "revoke_refresh_token", lambda t: state["rt"].pop(t, None))
    monkeypatch.setattr(session_store, "save_client", lambda cid, d: state["clients"].__setitem__(cid, d))
    monkeypatch.setattr(session_store, "load_client", lambda cid: state["clients"].get(cid))
    monkeypatch.setattr(session_store, "new_id", lambda: "sess-new")

    monkeypatch.setattr(config, "webapp_url", lambda: "https://app.staging.nrev.ai")
    monkeypatch.setattr(config, "hosted_issuer_url", lambda: "https://nrev-workflows-mcp.public.staging.nurturev.com")
    monkeypatch.setattr(config, "env_name", lambda: "staging")
    monkeypatch.setattr(config, "um_url", lambda: "https://umws.public.staging.nurturev.com")

    return state


class _FakeRequest:
    def __init__(self, method="POST", body=None):
        self.method = method
        self._body = body or {}

    async def json(self):
        return self._body


def _client(client_id="claude-desktop", redirect_uri="https://claude.ai/oauth/callback"):
    return OAuthClientInformationFull(client_id=client_id, redirect_uris=[redirect_uri])


def _params(redirect_uri="https://claude.ai/oauth/callback", state="claude-state-123"):
    return AuthorizationParams(
        state=state,
        scopes=["workflows"],
        code_challenge="challenge-abc",
        redirect_uri=redirect_uri,
        redirect_uri_provided_explicitly=True,
        resource=None,
    )


def _extract_nonce(authorize_url: str) -> str:
    outer = parse_qs(urlparse(authorize_url).query)
    final_redirect = outer["finalRedirect"][0]
    inner = parse_qs(urlparse(final_redirect).query)
    return inner["state"][0]


# ── dynamic client registration ─────────────────────────────────────────────


def test_register_and_get_client_round_trip(fake_store):
    provider = oauth.NrevOAuthProvider()
    client = _client()
    asyncio.run(provider.register_client(client))
    loaded = asyncio.run(provider.get_client("claude-desktop"))
    assert loaded is not None
    assert loaded.client_id == "claude-desktop"
    assert str(loaded.redirect_uris[0]) == "https://claude.ai/oauth/callback"


def test_get_unknown_client_returns_none(fake_store):
    provider = oauth.NrevOAuthProvider()
    assert asyncio.run(provider.get_client("nope")) is None


# ── authorize: hands off to the existing web app login ──────────────────────


def test_authorize_returns_webapp_login_url_and_stores_correlation(fake_store):
    provider = oauth.NrevOAuthProvider()
    client = _client()
    params = _params()

    url = asyncio.run(provider.authorize(client, params))

    assert url.startswith("https://app.staging.nrev.ai/login?finalRedirect=")
    nonce = _extract_nonce(url)
    assert nonce in fake_store["corr"]
    corr = fake_store["corr"][nonce]
    assert corr["client_id"] == "claude-desktop"
    assert corr["redirect_uri"] == "https://claude.ai/oauth/callback"
    assert corr["code_challenge"] == "challenge-abc"
    assert corr["state"] == "claude-state-123"


def test_authorize_callback_points_at_our_public_route(fake_store):
    provider = oauth.NrevOAuthProvider()
    url = asyncio.run(provider.authorize(_client(), _params()))
    # Double-encoded: cli_callback is quoted once as a query value inside
    # finalRedirect, then finalRedirect itself is quoted again as a query
    # value on the outer /login URL.
    assert "nrev-workflows-mcp.public.staging.nurturev.com%252Foauth%252Fwebapp-callback" in url


# ── the webapp's relay POST lands at handle_webapp_callback ─────────────────


def test_webapp_callback_rejects_missing_state(fake_store):
    resp = asyncio.run(oauth.handle_webapp_callback(_FakeRequest(body={"access_token": "x"})))
    assert resp.status_code == 400


def test_webapp_callback_rejects_unknown_state(fake_store):
    resp = asyncio.run(
        oauth.handle_webapp_callback(_FakeRequest(body={"state": "never-issued", "access_token": "x"}))
    )
    assert resp.status_code == 400


def test_webapp_callback_missing_access_token_redirects_with_error(fake_store):
    provider = oauth.NrevOAuthProvider()
    url = asyncio.run(provider.authorize(_client(), _params()))
    nonce = _extract_nonce(url)

    resp = asyncio.run(oauth.handle_webapp_callback(_FakeRequest(body={"state": nonce})))

    assert resp.status_code == 200
    import json

    payload = json.loads(resp.body)
    assert payload["ok"] is True
    assert payload["redirect_to"].startswith("https://claude.ai/oauth/callback?")
    assert "error=access_denied" in payload["redirect_to"]


def test_webapp_callback_success_stores_session_and_redirects_to_original_client(fake_store):
    provider = oauth.NrevOAuthProvider()
    url = asyncio.run(provider.authorize(_client(), _params(state="claude-state-123")))
    nonce = _extract_nonce(url)

    resp = asyncio.run(
        oauth.handle_webapp_callback(
            _FakeRequest(
                body={
                    "state": nonce,
                    "access_token": "sb-access",
                    "refresh_token": "sb-refresh",
                    "expires_in": 3600,
                    # Real shape sent by the web app's target=workflow handoff
                    # (WorkflowCliCallbackPayload) — same fields login.py's own
                    # handler reads directly, not via JWT decode.
                    "email": "user@nurturev.com",
                    "tenant_id": "137",
                }
            )
        )
    )

    assert resp.status_code == 200
    import json

    payload = json.loads(resp.body)
    assert payload["ok"] is True
    redirect_to = payload["redirect_to"]
    assert redirect_to.startswith("https://claude.ai/oauth/callback?")
    parsed = parse_qs(urlparse(redirect_to).query)
    assert parsed["state"][0] == "claude-state-123"  # the ORIGINAL client state, not our nonce
    assert "code" in parsed

    # The correlation nonce is single-use.
    assert nonce not in fake_store["corr"]
    # The underlying nRev session was persisted.
    assert fake_store["sessions"]["sess-new"]["access_token"] == "sb-access"
    assert fake_store["sessions"]["sess-new"]["refresh_token"] == "sb-refresh"
    # email/tenant_id come straight from the body, not JWT-decoded.
    assert fake_store["sessions"]["sess-new"]["user_info"] == {
        "email": "user@nurturev.com",
        "tenant": "137",
    }

    # Our minted auth code carries the session id as its subject.
    code = parsed["code"][0]
    assert fake_store["codes"][code]["subject"] == "sess-new"
    assert fake_store["codes"][code]["client_id"] == "claude-desktop"


# ── authorization code exchange (PKCE already verified by the SDK before
#    this is called — we don't re-verify it here, we test our own bookkeeping) ─


def test_exchange_authorization_code_is_single_use(fake_store):
    from mcp.server.auth.provider import AuthorizationCode

    provider = oauth.NrevOAuthProvider()
    client = _client()
    code_obj = AuthorizationCode(
        code="the-code",
        scopes=["workflows"],
        expires_at=time.time() + 300,
        client_id="claude-desktop",
        code_challenge="challenge-abc",
        redirect_uri="https://claude.ai/oauth/callback",
        redirect_uri_provided_explicitly=True,
        resource=None,
        subject="sess-a",
    )
    session_store.save_auth_code("the-code", {"subject": "sess-a", "client_id": "claude-desktop"})

    token = asyncio.run(provider.exchange_authorization_code(client, code_obj))
    assert token.access_token
    assert token.refresh_token
    assert token.token_type == "Bearer"  # OAuthToken normalizes casing
    assert fake_store["at"][token.access_token]["subject"] == "sess-a"

    # Single-use: the code record is gone after exchange.
    assert "the-code" not in fake_store["codes"]


def test_exchange_authorization_code_without_subject_raises(fake_store):
    from mcp.server.auth.provider import AuthorizationCode

    provider = oauth.NrevOAuthProvider()
    code_obj = AuthorizationCode(
        code="orphan-code",
        scopes=[],
        expires_at=time.time() + 300,
        client_id="claude-desktop",
        code_challenge="challenge-abc",
        redirect_uri="https://claude.ai/oauth/callback",
        redirect_uri_provided_explicitly=True,
        resource=None,
        subject=None,
    )
    with pytest.raises(TokenError):
        asyncio.run(provider.exchange_authorization_code(_client(), code_obj))


# ── refresh token exchange ───────────────────────────────────────────────────


def test_exchange_refresh_token_rotates_both_tokens_and_refreshes_session(fake_store, monkeypatch):
    from nrev_workflows_mcp import auth as auth_mod

    fake_store["sessions"]["sess-a"] = {
        "access_token": "old-nrev-token",
        "refresh_token": "old-nrev-refresh",
        "user_info": {},
        "expires_at": time.time() - 10,
        "env": "staging",
        "um_url": "https://umws.public.staging.nurturev.com",
    }

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "new-nrev-token", "refresh_token": "new-nrev-refresh", "expires_in": 3600}

    monkeypatch.setattr(auth_mod.httpx, "post", lambda *a, **k: _FakeResp())

    provider = oauth.NrevOAuthProvider()
    old_refresh = "mcp-refresh-old"
    session_store.save_refresh_token(old_refresh, {"client_id": "claude-desktop", "subject": "sess-a", "scopes": ["workflows"]})
    refresh_obj = asyncio.run(provider.load_refresh_token(_client(), old_refresh))
    assert refresh_obj is not None

    new_token = asyncio.run(provider.exchange_refresh_token(_client(), refresh_obj, []))

    assert new_token.access_token
    assert new_token.refresh_token
    assert new_token.refresh_token != old_refresh
    # Old refresh token is dead (rotated).
    assert old_refresh not in fake_store["rt"]
    # The underlying nRev session was refreshed too, not just the MCP-layer token.
    assert fake_store["sessions"]["sess-a"]["access_token"] == "new-nrev-token"


def test_load_refresh_token_rejects_client_mismatch(fake_store):
    session_store.save_refresh_token("rt-1", {"client_id": "claude-desktop", "subject": "sess-a", "scopes": []})
    provider = oauth.NrevOAuthProvider()
    other_client = _client(client_id="someone-else")
    assert asyncio.run(provider.load_refresh_token(other_client, "rt-1")) is None


# ── access token lookup ──────────────────────────────────────────────────────


def test_load_access_token_resolves_subject(fake_store):
    session_store.save_access_token("at-1", {"client_id": "claude-desktop", "scopes": [], "subject": "sess-a", "expires_at": time.time() + 3600})
    provider = oauth.NrevOAuthProvider()
    tok = asyncio.run(provider.load_access_token("at-1"))
    assert tok is not None
    assert tok.subject == "sess-a"


def test_load_access_token_rejects_expired(fake_store):
    session_store.save_access_token("at-expired", {"client_id": "claude-desktop", "scopes": [], "subject": "sess-a", "expires_at": time.time() - 1})
    provider = oauth.NrevOAuthProvider()
    assert asyncio.run(provider.load_access_token("at-expired")) is None
    # Expired token is cleaned up on lookup.
    assert "at-expired" not in fake_store["at"]
