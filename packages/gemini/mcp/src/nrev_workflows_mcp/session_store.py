"""Shared Redis-backed storage for the hosted (streamable-http) transport.

Not a performance cache — this is the multi-replica-safe persistence layer
that stands in for the local `~/.nrev-workflows/credentials` file once the
server runs as a shared, horizontally-scaled service instead of one process
per user's machine. Every record here needs to be visible to whichever
replica handles the next request from a given customer, and needs to survive
a pod restart/redeploy.

Deliberately plain functions (not a class), matching this codebase's existing
test idiom of monkeypatching module-level functions directly (see
`tests/test_tenant.py`'s `um` fixture). Deliberately synchronous, matching
every other module in this package (`auth.py`, `tenant.py`, `transport.py`
are all sync, using `httpx.Client` not `AsyncClient`) — a blocking Redis
round-trip is negligible next to the outbound HTTPS calls this server already
makes synchronously, and staying sync means `auth.py`/`tenant.py` don't need
an async rewrite just to read from here.

The Redis client is lazily constructed on first use, never at import time, so
importing this module (transitively, via `app.py` -> `oauth.py`) never dials
Redis on the stdio path, where none of this is used.

Key namespace: everything is prefixed `nrevmcp:` in case this Redis instance
is shared with other services (it is, in staging — see `config.py`).
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Optional

from . import config

_PREFIX = "nrevmcp:"

# TTLs, in seconds.
CORRELATION_TTL = 600  # 10 min — time budget for a user to complete login.
AUTH_CODE_TTL = 300  # 5 min — RFC 6749 recommends short-lived codes.
ACCESS_TOKEN_TTL = 3600  # 1 hour.
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 90  # ~90 days idle timeout; see plan's open risks.
TENANT_CACHE_TTL = 10  # matches tenant.py's in-process cache TTL today.

_client = None


def _redis():
    global _client
    if _client is None:
        import redis

        _client = redis.Redis(
            host=config.redis_host(),
            port=config.redis_port(),
            password=config.redis_password(),
            ssl=config.redis_ssl(),
            decode_responses=True,
        )
    return _client


def _hash(token: str) -> str:
    """Hash a bearer token before using it as a Redis key — never store raw
    tokens at rest, even in a private cache."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_id() -> str:
    return uuid.uuid4().hex


# ── correlation state (in-flight /authorize, keyed by our internal nonce) ──


def save_correlation(nonce: str, data: dict) -> None:
    _redis().set(f"{_PREFIX}oauth:corr:{nonce}", json.dumps(data), ex=CORRELATION_TTL)


def pop_correlation(nonce: str) -> Optional[dict]:
    """Single-use: fetch and delete atomically so a nonce can't be replayed."""
    key = f"{_PREFIX}oauth:corr:{nonce}"
    pipe = _redis().pipeline()
    pipe.get(key)
    pipe.delete(key)
    raw, _ = pipe.execute()
    return json.loads(raw) if raw else None


# ── our own authorization codes ─────────────────────────────────────────────


def save_auth_code(code: str, data: dict) -> None:
    _redis().set(f"{_PREFIX}oauth:code:{code}", json.dumps(data), ex=AUTH_CODE_TTL)


def load_auth_code(code: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}oauth:code:{code}")
    return json.loads(raw) if raw else None


def delete_auth_code(code: str) -> None:
    _redis().delete(f"{_PREFIX}oauth:code:{code}")


# ── the underlying nRev session (replaces ~/.nrev-workflows/credentials) ───


def save_session(session_id: str, data: dict) -> None:
    """`data` is the same shape as the local credentials file: access_token,
    refresh_token, user_info, expires_at, env, um_url."""
    _redis().set(f"{_PREFIX}session:{session_id}", json.dumps(data), ex=REFRESH_TOKEN_TTL)


def load_session(session_id: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}session:{session_id}")
    return json.loads(raw) if raw else None


def delete_session(session_id: str) -> None:
    _redis().delete(f"{_PREFIX}session:{session_id}")
    clear_tenant_state(session_id)


# ── issued MCP-client OAuth tokens -> session mapping ───────────────────────


def save_access_token(token: str, data: dict, ttl: int = ACCESS_TOKEN_TTL) -> None:
    """`data`: client_id, scopes, subject (session_id), expires_at."""
    _redis().set(f"{_PREFIX}oauth:at:{_hash(token)}", json.dumps(data), ex=ttl)


def load_access_token(token: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}oauth:at:{_hash(token)}")
    return json.loads(raw) if raw else None


def revoke_access_token(token: str) -> None:
    _redis().delete(f"{_PREFIX}oauth:at:{_hash(token)}")


def save_refresh_token(token: str, data: dict, ttl: int = REFRESH_TOKEN_TTL) -> None:
    """`data`: client_id, subject (session_id), scopes."""
    _redis().set(f"{_PREFIX}oauth:rt:{_hash(token)}", json.dumps(data), ex=ttl)


def load_refresh_token(token: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}oauth:rt:{_hash(token)}")
    return json.loads(raw) if raw else None


def revoke_refresh_token(token: str) -> None:
    _redis().delete(f"{_PREFIX}oauth:rt:{_hash(token)}")


# ── dynamically registered OAuth clients ────────────────────────────────────
# Not disposable cache — a client registers once and should stay registered
# indefinitely, so this key is written without a TTL. Flag to whoever owns
# Redis eviction policy if this instance uses an eviction scheme that could
# reap long-idle keys (e.g. volatile-lru is fine, allkeys-lru is not).


def save_client(client_id: str, data: dict) -> None:
    _redis().set(f"{_PREFIX}oauth:client:{client_id}", json.dumps(data))


def load_client(client_id: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}oauth:client:{client_id}")
    return json.loads(raw) if raw else None


# ── per-session tenant pin/cache (replaces tenant.py's process globals) ────


def save_tenant_pin(session_id: str, tenant: Optional[dict]) -> None:
    key = f"{_PREFIX}session:{session_id}:pinned"
    if tenant is None:
        _redis().delete(key)
    else:
        _redis().set(key, json.dumps(tenant))


def load_tenant_pin(session_id: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}session:{session_id}:pinned")
    return json.loads(raw) if raw else None


def save_tenant_cache(session_id: str, data: dict) -> None:
    _redis().set(
        f"{_PREFIX}session:{session_id}:tenant_cache", json.dumps(data), ex=TENANT_CACHE_TTL
    )


def load_tenant_cache(session_id: str) -> Optional[dict]:
    raw = _redis().get(f"{_PREFIX}session:{session_id}:tenant_cache")
    return json.loads(raw) if raw else None


def clear_tenant_state(session_id: str) -> None:
    _redis().delete(
        f"{_PREFIX}session:{session_id}:pinned",
        f"{_PREFIX}session:{session_id}:tenant_cache",
    )
