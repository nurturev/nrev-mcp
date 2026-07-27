"""Tests for auth.py's hosted (session-store) backend — the request-scoped
identity path used by the streamable-http transport. Session storage is
faked in-memory; request_state.hosted_identity is bound per test to simulate
which customer's request is "currently" being handled, the same way the
`mcp` SDK's auth middleware would bind it for a real incoming request.
"""
import time

import pytest

from nrev_workflows_mcp import auth, request_state, session_store


@pytest.fixture
def fake_sessions(monkeypatch):
    store = {}
    monkeypatch.setattr(session_store, "load_session", lambda sid: store.get(sid))
    monkeypatch.setattr(session_store, "save_session", lambda sid, data: store.__setitem__(sid, data))
    monkeypatch.setattr(session_store, "delete_session", lambda sid: store.pop(sid, None))
    return store


def _bind(monkeypatch, session_id):
    monkeypatch.setattr(request_state, "hosted_identity", lambda: session_id)


def _session(**overrides):
    base = {
        "access_token": "acc",
        "refresh_token": "ref",
        "user_info": {},
        "expires_at": time.time() + 3600,
        "env": "staging",
        "um_url": "https://um.example",
    }
    base.update(overrides)
    return base


def test_get_jwt_reads_hosted_session(fake_sessions, monkeypatch):
    fake_sessions["sess-a"] = _session(access_token="acc-a")
    _bind(monkeypatch, "sess-a")
    assert auth.get_jwt() == "acc-a"


def test_get_jwt_raises_hosted_specific_hint_when_missing(fake_sessions, monkeypatch):
    _bind(monkeypatch, "sess-missing")
    with pytest.raises(auth.AuthError, match="Reconnect the nrev-workflows connector"):
        auth.get_jwt()


def test_refresh_near_expiry_rewrites_hosted_session(fake_sessions, monkeypatch):
    fake_sessions["sess-a"] = _session(access_token="old", refresh_token="ref-old", expires_at=time.time() - 10)
    _bind(monkeypatch, "sess-a")

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "new", "refresh_token": "ref-new", "expires_in": 3600}

    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: _FakeResp())
    assert auth.refresh_if_needed() == "new"
    assert fake_sessions["sess-a"]["access_token"] == "new"
    assert fake_sessions["sess-a"]["refresh_token"] == "ref-new"


def test_status_reports_hosted_session(fake_sessions, monkeypatch):
    fake_sessions["sess-a"] = _session(user_info={"email": "a@b.co", "tenant": "t1"})
    _bind(monkeypatch, "sess-a")
    out = auth.status()
    assert out["status"] == "set"
    assert out["source"] == "session"
    assert out["email"] == "a@b.co"
    assert out["tenant"] == "t1"


def test_no_crossover_between_concurrent_sessions(fake_sessions, monkeypatch):
    """The direct regression test for the bug this whole redesign exists to
    fix: auth.py used to keep a single process-wide override/session state,
    which would leak between two customers' concurrent requests on a shared
    server. Binding identity to session A then session B must never let B
    see A's token, or vice versa."""
    fake_sessions["sess-a"] = _session(access_token="token-a", refresh_token="ref-a")
    fake_sessions["sess-b"] = _session(access_token="token-b", refresh_token="ref-b")

    _bind(monkeypatch, "sess-a")
    assert auth.get_jwt() == "token-a"

    _bind(monkeypatch, "sess-b")
    assert auth.get_jwt() == "token-b"

    _bind(monkeypatch, "sess-a")
    assert auth.get_jwt() == "token-a"  # still A's token — not leaked/overwritten by B


def test_stdio_path_unaffected_when_no_hosted_identity(monkeypatch, tmp_path):
    """hosted_identity() returning None (the stdio default) must fall all the
    way back to the local-file backend, completely untouched by this module
    having ever been imported."""
    monkeypatch.setenv("NREV_WORKFLOWS_DIR", str(tmp_path))
    monkeypatch.setattr(request_state, "hosted_identity", lambda: None)
    assert auth.status() == {"status": "unset"}
    auth.save_credentials("local-acc", "local-ref", {}, time.time() + 3600)
    assert auth.get_jwt() == "local-acc"
