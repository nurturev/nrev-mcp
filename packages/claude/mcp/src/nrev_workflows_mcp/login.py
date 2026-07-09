"""Browser login via the nRev web app (relay flow).

The CLI opens the web app's login page with a ``finalRedirect`` to
``/cli/auth/done``, passing a CSRF ``state`` nonce, a localhost ``cli_callback``,
and ``target=workflow``. The user signs in (Supabase, handled entirely by the web
app), and the ``/cli/auth/done`` page POSTs the user's **Supabase** session
(access + refresh) to the CLI's localhost callback. We validate the echoed state
and persist the session via :func:`auth.save_credentials`.

The MCP never talks to Supabase: the web app holds the IdP relationship, and
refresh later goes through user-management. Because the web app (not Supabase)
forwards to localhost, the callback port is free-choice — no redirect allow-list
applies to it.
"""
from __future__ import annotations

import json
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Optional
from urllib.parse import quote, urlparse

from . import auth, config


class LoginError(RuntimeError):
    pass


class _Result:
    def __init__(self, expected_state: str) -> None:
        self.expected_state = expected_state
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at = None
        self.expires_in = None
        self.user_info: Optional[dict] = None
        self.error: Optional[str] = None
        self.received = threading.Event()


def _make_handler(result: _Result):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self) -> None:
            # The done-page is served from the web app origin (https) and POSTs
            # cross-origin to this loopback server; allow it.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/callback":
                self.send_response(404)
                self._cors()
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}

            if body.get("error"):
                result.error = str(body["error"])
            elif body.get("state") != result.expected_state:
                result.error = "state mismatch (possible CSRF) — login aborted"
            else:
                result.access_token = body.get("access_token")
                result.refresh_token = body.get("refresh_token")
                result.expires_at = body.get("expires_at")
                result.expires_in = body.get("expires_in")
                result.user_info = {
                    "email": body.get("email"),
                    "tenant": body.get("tenant_id"),
                }
                if not result.access_token:
                    result.error = "no access token in the handoff payload"

            ok = result.error is None
            self.send_response(200 if ok else 400)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "error": result.error}).encode())
            result.received.set()

        def do_GET(self) -> None:  # noqa: N802 — friendly page if hit directly
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;"
                b"padding-top:80px'><h2>nrev-workflows login callback. "
                b"You can close this tab.</h2></body></html>"
            )

        def log_message(self, *args) -> None:  # silence HTTP logs
            pass

    return Handler


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def login(
    on_url: Optional[Callable[[str], None]] = None,
    open_browser: bool = True,
    wait_timeout: int = 180,
) -> dict:
    """Run the web-app relay login and persist the session.

    ``on_url`` (if given) is called with the login URL before we block waiting
    for the handoff — the CLI uses it to print a manual fallback link. Returns
    the resulting :func:`auth.status` dict; raises :class:`LoginError`.
    """
    webapp = config.webapp_url()
    port = _find_free_port()
    cli_callback = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(32)

    result = _Result(expected_state=state)
    server = HTTPServer(("127.0.0.1", port), _make_handler(result))
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        final_redirect = (
            f"/cli/auth/done?state={state}"
            f"&cli_callback={quote(cli_callback, safe='')}"
            f"&target=workflow"
        )
        login_url = f"{webapp}/login?finalRedirect={quote(final_redirect, safe='')}"
        if on_url:
            on_url(login_url)
        if open_browser:
            webbrowser.open(login_url)

        result.received.wait(timeout=wait_timeout)
    finally:
        server.shutdown()

    if not result.received.is_set():
        raise LoginError("Timed out waiting for the login handoff.")
    if result.error:
        raise LoginError(result.error)
    if not result.access_token:
        raise LoginError("No access token received from the web app handoff.")

    expires_at = result.expires_at or (time.time() + float(result.expires_in or 3600))
    auth.save_credentials(
        access_token=result.access_token,
        refresh_token=result.refresh_token or "",
        user_info=result.user_info or {},
        expires_at=float(expires_at),
        env=config.env_name(),
        um_url=config.um_url(),
    )
    return auth.status()
