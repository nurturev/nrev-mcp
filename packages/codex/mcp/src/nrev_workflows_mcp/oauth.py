"""OAuth 2.1 authorization server for the hosted (streamable-http) transport.

Implements `OAuthAuthorizationServerProvider` so Cowork/Claude can treat this
server as a normal OAuth resource server — the `mcp` SDK provides
`.well-known` discovery, `/authorize`, `/token`, and `/register` once this is
wired into `FastMCP(auth_server_provider=..., auth=AuthSettings(...))` (see
`app.py`).

The actual authentication step is NOT reimplemented here — `authorize()`
hands off to the exact nRev web app login `login.py` already uses for the
CLI, just with the callback re-pointed at our own public
`/oauth/webapp-callback` route instead of `localhost` (the web app's
`/cli/auth/done` page allow-lists this server's hostname explicitly — see
nrev-ui-2's `TRUSTED_CLI_CALLBACK_HOSTS`).

Flow:
  1. Cowork calls /authorize. `authorize()` stores a correlation record
     (Cowork's client_id/redirect_uri/state/code_challenge) under a nonce we
     mint, and returns the nRev web app login URL with `cli_callback` pointed
     at our webapp-callback route, carrying that nonce as its own `state`.
  2. The user logs in on the web app (Supabase, unchanged). The web app POSTs
     the resulting session to `handle_webapp_callback`.
  3. `handle_webapp_callback` looks up the correlation record by nonce,
     stores the real nRev session in `session_store`, mints our own
     authorization code, and responds with `{ok: true, redirect_to: ...}` —
     JSON, not an HTTP redirect, because the web app reaches this via
     `fetch()`, which can't drive the browser's actual location. The web
     app's page navigates the browser there itself (see nrev-ui-2's
     `hooks.ts`).
  4. Cowork's browser lands on its own redirect_uri with our code, and
     exchanges it at /token. The SDK verifies PKCE itself before
     `exchange_authorization_code()` is ever called.
  5. Every later tool call carries our issued access token; `load_access_token`
     resolves it to a session id, surfaced to `auth.py`/`tenant.py` via
     `request_state.hosted_identity()`.
"""
from __future__ import annotations

import secrets
import time
from typing import Optional
from urllib.parse import quote

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import auth, config, session_store


def _webapp_callback_url() -> str:
    return f"{config.hosted_issuer_url()}/oauth/webapp-callback"


def _cors_origin() -> str:
    return config.webapp_url()


def _json_with_cors(payload: dict, status_code: int = 200) -> JSONResponse:
    resp = JSONResponse(payload, status_code=status_code)
    resp.headers["Access-Control-Allow-Origin"] = _cors_origin()
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _handoff_expires_at(body: dict) -> float:
    if body.get("expires_at"):
        return float(body["expires_at"])
    return time.time() + float(body.get("expires_in", 3600))


async def handle_webapp_callback(request: Request) -> Response:
    """The nRev web app's /cli/auth/done relay POSTs here (registered as an
    unauthenticated custom_route in app.py — never touches the OAuth bearer
    auth layer, same trust boundary login.py's localhost listener has)."""
    if request.method == "OPTIONS":
        return _json_with_cors({})

    try:
        body = await request.json()
    except Exception:
        return _json_with_cors({"ok": False, "error": "invalid_request"}, 400)

    nonce = body.get("state")
    if not nonce:
        return _json_with_cors({"ok": False, "error": "missing_state"}, 400)

    corr = session_store.pop_correlation(nonce)  # single-use
    if corr is None:
        return _json_with_cors({"ok": False, "error": "unknown_or_expired_state"}, 400)

    if body.get("error") or not body.get("access_token"):
        redirect_to = construct_redirect_uri(
            corr["redirect_uri"], error="access_denied", state=corr.get("state")
        )
        return _json_with_cors({"ok": True, "redirect_to": redirect_to})

    session_id = session_store.new_id()
    # The web app's target=workflow handoff (same one login.py uses) sends
    # email/tenant_id directly in the body — mirror login.py's own handler
    # rather than decoding the JWT, which isn't guaranteed to carry these as
    # claims.
    session_store.save_session(
        session_id,
        {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "user_info": {"email": body.get("email"), "tenant": body.get("tenant_id")},
            "expires_at": _handoff_expires_at(body),
            "env": config.env_name(),
            "um_url": config.um_url(),
        },
    )

    code = secrets.token_urlsafe(32)
    session_store.save_auth_code(
        code,
        {
            "client_id": corr["client_id"],
            "code_challenge": corr["code_challenge"],
            "redirect_uri": corr["redirect_uri"],
            "redirect_uri_provided_explicitly": corr["redirect_uri_provided_explicitly"],
            "scopes": corr["scopes"],
            "resource": corr.get("resource"),
            "subject": session_id,
            "expires_at": time.time() + session_store.AUTH_CODE_TTL,
        },
    )

    redirect_to = construct_redirect_uri(corr["redirect_uri"], code=code, state=corr.get("state"))
    return _json_with_cors({"ok": True, "redirect_to": redirect_to})


def _issue_tokens(client_id: str, scopes: list[str], subject: str) -> OAuthToken:
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    now = int(time.time())
    session_store.save_access_token(
        access_token,
        {
            "client_id": client_id,
            "scopes": scopes,
            "subject": subject,
            "expires_at": now + session_store.ACCESS_TOKEN_TTL,
        },
    )
    session_store.save_refresh_token(
        refresh_token, {"client_id": client_id, "subject": subject, "scopes": scopes}
    )
    return OAuthToken(
        access_token=access_token,
        token_type="bearer",
        expires_in=session_store.ACCESS_TOKEN_TTL,
        refresh_token=refresh_token,
        scope=" ".join(scopes) if scopes else None,
    )


class NrevOAuthProvider(OAuthAuthorizationServerProvider):
    """Delegates authentication to the nRev web app's existing browser-relay
    login (`handle_webapp_callback` above); only OAuth-protocol bookkeeping
    (codes/tokens/clients) lives here."""

    # ── dynamic client registration ─────────────────────────────────────

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        data = session_store.load_client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        session_store.save_client(client_info.client_id, client_info.model_dump(mode="json"))

    # ── authorize: hand off to the existing web app login ───────────────

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        nonce = secrets.token_urlsafe(32)
        session_store.save_correlation(
            nonce,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "state": params.state,
                "code_challenge": params.code_challenge,
                "scopes": params.scopes or [],
                "resource": params.resource,
            },
        )
        final_redirect = (
            f"/cli/auth/done?state={nonce}"
            f"&cli_callback={quote(_webapp_callback_url(), safe='')}"
            f"&target=workflow"
        )
        return f"{config.webapp_url()}/login?finalRedirect={quote(final_redirect, safe='')}"

    # ── authorization code lifecycle ─────────────────────────────────────

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        data = session_store.load_auth_code(authorization_code)
        if data is None or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=data["scopes"],
            expires_at=data["expires_at"],
            client_id=data["client_id"],
            code_challenge=data["code_challenge"],
            redirect_uri=data["redirect_uri"],
            redirect_uri_provided_explicitly=data["redirect_uri_provided_explicitly"],
            resource=data.get("resource"),
            subject=data["subject"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        session_store.delete_auth_code(authorization_code.code)  # single-use
        if authorization_code.subject is None:
            raise TokenError("invalid_grant", "authorization code has no associated session")
        return _issue_tokens(client.client_id, authorization_code.scopes, authorization_code.subject)

    # ── refresh token lifecycle ──────────────────────────────────────────

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        data = session_store.load_refresh_token(refresh_token)
        if data is None or data["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=None,
            subject=data["subject"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.subject is None:
            raise TokenError("invalid_grant", "refresh token has no associated session")
        # Also refresh the underlying nRev session so it doesn't go stale even
        # while the MCP-client token itself is being actively kept alive.
        auth.refresh_session_by_id(refresh_token.subject)
        session_store.revoke_refresh_token(refresh_token.token)  # rotate: old one dies
        effective_scopes = scopes or refresh_token.scopes
        return _issue_tokens(client.client_id, effective_scopes, refresh_token.subject)

    # ── access token lookup (per-request identity) ───────────────────────

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        data = session_store.load_access_token(token)
        if data is None:
            return None
        expires_at = data.get("expires_at")
        if expires_at and expires_at < time.time():
            session_store.revoke_access_token(token)
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            # AccessToken.expires_at is int-only; data comes back from JSON in
            # session_store, which doesn't enforce that a stored number stayed
            # an int — cast defensively rather than trust the stored shape.
            expires_at=int(expires_at) if expires_at else None,
            resource=None,
            subject=data["subject"],
            claims=None,
        )

    async def revoke_token(self, token) -> None:
        if isinstance(token, AccessToken):
            session_store.revoke_access_token(token.token)
        elif isinstance(token, RefreshToken):
            session_store.revoke_refresh_token(token.token)
