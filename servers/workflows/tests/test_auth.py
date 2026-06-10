"""Tests for the in-memory JWT store."""
import base64
import json
import time

import pytest

from nrev_workflows_mcp import auth


@pytest.fixture(autouse=True)
def clean():
    auth.reset()
    yield
    auth.reset()


def make_jwt(exp_offset_seconds: int = 3600) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + exp_offset_seconds}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.sig"


def test_unset_status_and_error():
    assert auth.status() == {"status": "unset"}
    with pytest.raises(auth.AuthError, match="JWT not set"):
        auth.get_jwt()


def test_set_strips_bearer_prefix_and_decodes_exp():
    token = make_jwt(600)
    out = auth.set_jwt(f"Bearer {token}")
    assert out["status"] == "set"
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
