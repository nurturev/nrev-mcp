"""Tests for the auth store: manual override + persistent auto-refreshing Supabase session."""
import base64
import json
import time

import pytest

from nrev_workflows_mcp import auth


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    # Isolate credentials on disk and the environment so tests are hermetic.
    monkeypatch.setenv("NREV_WORKFLOWS_DIR", str(tmp_path))
    monkeypatch.setenv("NREV_ENV", "staging")
    monkeypatch.delenv("NREV_JWT", raising=False)
    auth.reset()
    yield
    auth.reset()


def make_jwt(exp_offset_seconds: int = 3600, **claims) -> str:
    payload = {"exp": int(time.time()) + exp_offset_seconds, **claims}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{body}.sig"


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# ── manual override (unchanged behaviour) ──────────────────────────────────


def test_unset_status_and_error():
    assert auth.status() == {"status": "unset"}
    with pytest.raises(auth.AuthError, match="JWT not set"):
        auth.get_jwt()


def test_set_strips_bearer_prefix_and_decodes_exp():
    token = make_jwt(600)
    out = auth.set_jwt(f"Bearer {token}")
    assert out["status"] == "set"
    assert out["source"] == "manual"
    assert out["expired"] is False
    assert 8 <= out["expires_in_minutes"] <= 10
    assert auth.get_jwt() == token


def test_expired_token_flagged():
    auth.set_jwt(make_jwt(-60))
    assert auth.status()["expired"] is True


def test_empty_token_rejected():
    with pytest.raises(auth.AuthError):
        auth.set_jwt("   ")


def test_seed_from_env(monkeypatch):
    monkeypatch.setenv("NREV_JWT", make_jwt())
    assert auth.seed_from_env() is True
    assert auth.status()["status"] == "set"
    monkeypatch.setenv("NREV_JWT", "")
    auth.reset()
    assert auth.seed_from_env() is False


# ── persistent Supabase session ─────────────────────────────────────────────


def test_save_load_clear_credentials():
    auth.save_credentials("acc", "ref", {"email": "a@b.co"}, time.time() + 3600)
    creds = auth.load_credentials()
    assert creds["access_token"] == "acc"
    assert creds["refresh_token"] == "ref"
    assert creds["env"] == "staging"
    assert "umws" in creds["um_url"]
    auth.clear_credentials()
    assert auth.load_credentials() is None


def test_status_reports_session_identity_from_claims():
    token = make_jwt(3600, email="a@b.co", tenant_id="t-42")
    auth.save_credentials(token, "ref", {}, time.time() + 3600)
    out = auth.status()
    assert out["source"] == "session"
    assert out["email"] == "a@b.co"
    assert out["tenant"] == "t-42"
    assert out["expired"] is False
    assert out["auto_refresh"] is True


def test_get_jwt_uses_session_when_no_override():
    auth.save_credentials("acc", "ref", {}, time.time() + 3600)
    assert auth.get_jwt() == "acc"


def test_override_wins_over_session():
    auth.save_credentials("session-token", "ref", {}, time.time() + 3600)
    auth.set_jwt("manual-token")
    assert auth.get_jwt() == "manual-token"
    assert auth.status()["source"] == "manual"


def test_refresh_if_needed_returns_cached_when_fresh(monkeypatch):
    auth.save_credentials("acc", "ref", {}, time.time() + 3600)
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: pytest.fail("refreshed early"))
    assert auth.refresh_if_needed() == "acc"


def test_refresh_when_expired_calls_um(monkeypatch):
    auth.save_credentials("old", "ref-old", {"email": "a@b.co"}, time.time() - 10)
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _FakeResp(
            {"access_token": "new", "refresh_token": "ref-new", "expires_in": 3600}
        )

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    assert auth.refresh_if_needed() == "new"
    assert captured["url"].endswith("/auth/cli/refresh")  # user-management, not Supabase
    assert captured["body"] == {"refresh_token": "ref-old"}
    creds = auth.load_credentials()
    assert creds["access_token"] == "new"
    assert creds["refresh_token"] == "ref-new"  # rotated
    assert creds["expires_at"] > time.time()


def test_refresh_failure_falls_back_to_existing(monkeypatch):
    import httpx

    auth.save_credentials("old", "ref-old", {}, time.time() - 10)
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    assert auth.refresh_if_needed() == "old"


def test_force_refresh(monkeypatch):
    auth.save_credentials("old", "ref-old", {}, time.time() + 3600)
    monkeypatch.setattr(
        auth.httpx, "post",
        lambda *a, **k: _FakeResp({"access_token": "forced", "refresh_token": "r2", "expires_in": 100}),
    )
    assert auth.force_refresh() == "forced"


def test_env_mismatch_warning(monkeypatch):
    auth.save_credentials("acc", "ref", {}, time.time() + 3600, env="staging")
    monkeypatch.setenv("NREV_ENV", "prod")
    out = auth.status()
    assert "env_mismatch" in out


def test_env_follows_session_when_unset(monkeypatch):
    from nrev_workflows_mcp import config

    monkeypatch.delenv("NREV_ENV", raising=False)
    assert config.env_name() == "prod"  # no session yet → default
    auth.save_credentials("acc", "ref", {}, time.time() + 3600, env="staging")
    assert config.env_name() == "staging"  # server follows the logged-in env
    monkeypatch.setenv("NREV_ENV", "prod")
    assert config.env_name() == "prod"  # explicit NREV_ENV still wins
