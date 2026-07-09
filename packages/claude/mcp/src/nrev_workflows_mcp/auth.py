"""Authentication: a persistent, auto-refreshing platform session plus a manual override.

A bearer token comes from one of two sources, in priority order:

  1. A manual override — the ``set_jwt`` tool or the ``NREV_JWT`` env var. Opaque
     and not refreshable; kept as an escape hatch (CI, or pasting a token straight
     from the platform web app).
  2. A persisted session at ``~/.nrev-workflows/credentials``, created by
     ``nrev-workflows auth login`` (or the ``auth_login`` tool). The token is a
     genuine Supabase session relayed from the web app through user-management,
     so the workflow API + tables service accept it directly. It is refreshed
     automatically via user-management — the MCP never talks to Supabase and
     holds no Supabase keys.

The credentials file is JSON, chmod 600, and records the environment + the
user-management host it was issued against so refreshes hit the right place and
we can warn on a mismatch.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Optional

import httpx

from . import config

# In-memory manual override (set_jwt / NREV_JWT). Never persisted.
_override: Optional[str] = None
_override_exp: Optional[int] = None

# Refresh the access token this many seconds before it expires.
_REFRESH_BUFFER_SECONDS = 120


class AuthError(RuntimeError):
    pass


def _decode_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature (for display only)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _decode_exp(token: str) -> Optional[int]:
    exp = _decode_claims(token).get("exp")
    return int(exp) if exp else None


# ── persistent credentials ───────────────────────────────────────────────


def save_credentials(
    access_token: str,
    refresh_token: str,
    user_info: dict[str, Any],
    expires_at: float,
    env: Optional[str] = None,
    um_url: Optional[str] = None,
) -> None:
    """Persist the session to ~/.nrev-workflows/credentials (chmod 600)."""
    path = config.credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user_info": user_info or {},
        "expires_at": expires_at,
        "env": env or config.env_name(),
        "um_url": um_url or config.um_url(),
    }
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def load_credentials() -> Optional[dict]:
    """Load the persisted session, or None if missing/corrupt."""
    path = config.credentials_file()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def clear_credentials() -> None:
    """Delete the persisted session (logout)."""
    path = config.credentials_file()
    if path.exists():
        path.unlink()


def _expires_at(data: dict) -> float:
    """Pick the absolute expiry from a refresh response."""
    if data.get("expires_at"):
        return float(data["expires_at"])
    return time.time() + float(data.get("expires_in", 3600))


def _refresh(creds: dict) -> Optional[str]:
    """Refresh the access token via user-management. Returns it or None.

    user-management proxies to Supabase (which rotates the refresh token) and
    returns the new pair, so the MCP never holds a Supabase key. We re-persist
    the rotated pair. Failure (network, expired/invalid refresh token) returns
    None — the caller decides how to surface it.
    """
    refresh_tok = creds.get("refresh_token")
    if not refresh_tok:
        return None
    um = creds.get("um_url") or config.um_url()
    try:
        resp = httpx.post(
            f"{um}/auth/cli/refresh",
            json={"refresh_token": refresh_tok},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    access = data.get("access_token")
    if not access:
        return None
    save_credentials(
        access_token=access,
        refresh_token=data.get("refresh_token", refresh_tok),
        user_info=creds.get("user_info", {}),
        expires_at=_expires_at(data),
        env=creds.get("env"),
        um_url=um,
    )
    return access


def refresh_if_needed() -> Optional[str]:
    """Return the session access token, refreshing it first if near expiry."""
    creds = load_credentials()
    if creds is None:
        return None
    access = creds.get("access_token")
    expires_at = creds.get("expires_at", 0)
    if access and time.time() < expires_at - _REFRESH_BUFFER_SECONDS:
        return access
    # Near/at expiry — try to refresh, but fall back to the existing token so a
    # transient refresh failure still lets the request attempt + 401-retry run.
    return _refresh(creds) or access


def force_refresh() -> Optional[str]:
    """Refresh regardless of expiry — used to retry a request that 401'd."""
    creds = load_credentials()
    if creds is None:
        return None
    return _refresh(creds)


# ── token access / manual override ─────────────────────────────────────────


def set_jwt(token: str) -> dict:
    """Store a manual override JWT. Accepts a bare token or `Bearer <token>`."""
    global _override, _override_exp
    token = (token or "").strip()
    if not token:
        raise AuthError("token is empty")
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    _override = token
    _override_exp = _decode_exp(token)
    return status()


def get_jwt() -> str:
    """Return the current bearer token: manual override first, else session."""
    if _override is not None:
        return _override
    token = refresh_if_needed()
    if token:
        return token
    raise AuthError(
        "JWT not set — no active session. Run `nrev-workflows auth login` (or "
        "the auth_login tool) to sign in once; the token then refreshes "
        "automatically. As a fallback, call set_jwt(token) with a token copied "
        "from the platform web app (DevTools → Network → Authorization header)."
    )


def status() -> dict:
    """Report auth state: source, identity, and expiry."""
    if _override is not None:
        out: dict = {"status": "set", "source": "manual", "last4": _override[-4:]}
        if _override_exp is not None:
            now = int(time.time())
            out["expires_at_unix"] = _override_exp
            out["expires_in_minutes"] = max(0, (_override_exp - now) // 60)
            out["expired"] = _override_exp < now
        return out

    creds = load_credentials()
    if creds is None:
        return {"status": "unset"}

    info = creds.get("user_info", {}) or {}
    claims = _decode_claims(creds.get("access_token", ""))
    expires_at = float(creds.get("expires_at", 0) or 0)
    now = time.time()
    out = {
        "status": "set",
        "source": "session",
        "email": info.get("email") or claims.get("email"),
        "tenant": info.get("tenant") or claims.get("tenant_id"),
        "env": creds.get("env"),
        "expires_at_unix": int(expires_at),
        "expires_in_minutes": max(0, int((expires_at - now) // 60)),
        "expired": expires_at < now,
        "auto_refresh": bool(creds.get("refresh_token")),
    }
    session_env = creds.get("env")
    if session_env and session_env != config.env_name():
        out["env_mismatch"] = (
            f"session was issued for '{session_env}' but NREV_ENV is "
            f"'{config.env_name()}' — the token won't validate against the "
            f"'{config.env_name()}' workflow API. Re-run `nrev-workflows auth "
            f"login` for this environment."
        )
    return out


def seed_from_env() -> bool:
    """Load NREV_JWT as a manual override if present. Returns True if loaded."""
    token = os.environ.get("NREV_JWT", "").strip()
    if token:
        set_jwt(token)
        return True
    return False


def reset() -> None:
    """Clear the in-memory override (does not touch the persisted session)."""
    global _override, _override_exp
    _override = None
    _override_exp = None
