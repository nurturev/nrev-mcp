"""Authentication: a persistent, auto-refreshing platform session.

A bearer token comes from a persisted session, created by
``nrev-workflows auth login`` (or the ``auth_login`` tool) on the stdio
transport, or by the OAuth connector flow (see ``oauth.py``) on the hosted
transport. Either way it's a genuine Supabase session relayed from the web
app through user-management, so the workflow API + tables service accept it
directly. It is refreshed automatically via user-management — this module
never talks to Supabase and holds no Supabase keys.

Two storage backends, selected transparently per request via
``request_state.hosted_identity()``:

  - stdio (local): the session lives in a JSON file at
    ``~/.nrev-workflows/credentials`` (chmod 600), one per machine/user.
  - hosted (streamable-http): the session lives in Redis
    (``session_store.py``), keyed by the OAuth-resolved session id, since a
    shared multi-replica service can't rely on local disk — and unlike the
    local-file case, this state must survive across a customer's separate
    tool calls (separate HTTP requests) and across pod restarts.

The refresh/expiry logic (``_refresh_data``) is written once and shared by
both backends — only *where* the resulting session gets persisted differs.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Optional

import httpx

from . import config, request_state

# Refresh the access token this many seconds before it expires.
_REFRESH_BUFFER_SECONDS = 120


class AuthError(RuntimeError):
    pass


def decode_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature (for display only)."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


def _decode_exp(token: str) -> Optional[int]:
    exp = decode_claims(token).get("exp")
    return int(exp) if exp else None


# ── local-file backend (stdio transport) ─────────────────────────────────


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


# ── backend seam: local file vs. hosted session store ───────────────────
#
# Everything below this point is transport-agnostic: it reads/writes through
# whichever backend `_backend()` resolves, so the refresh/expiry logic exists
# exactly once. `request_state.hosted_identity()` returns None on stdio
# (there is no HTTP request, no auth middleware) and a session id on the
# hosted transport (set per-request by the `mcp` SDK's own auth middleware).


class _Backend:
    def load(self) -> Optional[dict]:
        raise NotImplementedError

    def save(self, data: dict) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class _LocalFileBackend(_Backend):
    def load(self) -> Optional[dict]:
        return load_credentials()

    def save(self, data: dict) -> None:
        save_credentials(**data)

    def clear(self) -> None:
        clear_credentials()


class _SessionStoreBackend(_Backend):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id

    def load(self) -> Optional[dict]:
        from . import session_store

        return session_store.load_session(self.session_id)

    def save(self, data: dict) -> None:
        from . import session_store

        session_store.save_session(self.session_id, data)

    def clear(self) -> None:
        from . import session_store

        session_store.delete_session(self.session_id)


def _backend() -> _Backend:
    session_id = request_state.hosted_identity()
    if session_id is not None:
        return _SessionStoreBackend(session_id)
    return _LocalFileBackend()


def _expires_at(data: dict) -> float:
    """Pick the absolute expiry from a refresh response."""
    if data.get("expires_at"):
        return float(data["expires_at"])
    return time.time() + float(data.get("expires_in", 3600))


def _refresh_data(creds: dict) -> Optional[dict]:
    """Call user-management to refresh, returning the new full creds dict
    (not yet persisted anywhere), or None on failure (network, expired/
    invalid refresh token) — the caller decides how to surface that and
    where the result gets saved.

    user-management proxies to Supabase (which rotates the refresh token) and
    returns the new pair, so this module never holds a Supabase key.
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
    return {
        "access_token": access,
        "refresh_token": data.get("refresh_token", refresh_tok),
        "user_info": creds.get("user_info", {}),
        "expires_at": _expires_at(data),
        "env": creds.get("env"),
        "um_url": um,
    }


def refresh_session_by_id(session_id: str) -> Optional[dict]:
    """Force-refresh a specific hosted session by id, returning the new creds
    dict (or None on failure). Used by ``oauth.py``'s refresh_token exchange,
    which runs before any request-context identity exists — there's no
    incoming Bearer token yet at that point, so ``_backend()``'s context
    resolution doesn't apply."""
    from . import session_store

    creds = session_store.load_session(session_id)
    if creds is None:
        return None
    new_creds = _refresh_data(creds)
    if new_creds is None:
        return None
    session_store.save_session(session_id, new_creds)
    return new_creds


def refresh_if_needed() -> Optional[str]:
    """Return the session access token, refreshing it first if near expiry."""
    backend = _backend()
    creds = backend.load()
    if creds is None:
        return None
    access = creds.get("access_token")
    expires_at = creds.get("expires_at", 0)
    if access and time.time() < expires_at - _REFRESH_BUFFER_SECONDS:
        return access
    # Near/at expiry — try to refresh, but fall back to the existing token so a
    # transient refresh failure still lets the request attempt + 401-retry run.
    new_creds = _refresh_data(creds)
    if new_creds is None:
        return access
    backend.save(new_creds)
    return new_creds["access_token"]


def force_refresh() -> Optional[str]:
    """Refresh regardless of expiry — used to retry a request that 401'd."""
    backend = _backend()
    creds = backend.load()
    if creds is None:
        return None
    new_creds = _refresh_data(creds)
    if new_creds is None:
        return None
    backend.save(new_creds)
    return new_creds["access_token"]


# ── token access ─────────────────────────────────────────────────────────


def get_jwt() -> str:
    """Return the current bearer token, refreshing the session if needed."""
    token = refresh_if_needed()
    if token:
        return token
    if request_state.hosted_identity() is not None:
        raise AuthError(
            "Session not found or expired. Reconnect the nrev-workflows "
            "connector in your Claude/Cowork connector settings to sign in again."
        )
    raise AuthError(
        "JWT not set — no active session. Run `nrev-workflows auth login` (or "
        "the auth_login tool) to sign in once; the token then refreshes "
        "automatically."
    )


def status() -> dict:
    """Report auth state: identity and expiry."""
    backend = _backend()
    creds = backend.load()
    if creds is None:
        return {"status": "unset"}

    info = creds.get("user_info", {}) or {}
    claims = decode_claims(creds.get("access_token", ""))
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
