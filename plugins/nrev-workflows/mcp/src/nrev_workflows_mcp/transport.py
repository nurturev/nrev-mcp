"""Shared HTTP plumbing for the workflow and tables APIs.

One place to inject the JWT header and to surface API errors as a typed
exception carrying the URL + response body, so every tool failure is
actionable for the calling agent.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx

from . import auth

TIMEOUT_SECONDS = float(os.environ.get("NREV_TIMEOUT", "60"))


class APIError(RuntimeError):
    def __init__(self, status_code: int, body: str, url: str):
        super().__init__(f"HTTP {status_code} from {url}: {body[:500]}")
        self.status_code = status_code
        self.body = body
        self.url = url


def _send(
    host: str,
    token: str,
    method: str,
    path: str,
    json_body: Optional[dict],
    params: Optional[dict],
) -> httpx.Response:
    with httpx.Client(
        base_url=host,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
    ) as client:
        return client.request(method, path, json=json_body, params=params)


def request(
    host: str,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    token = auth.get_jwt()
    r = _send(host, token, method, path, json_body, params)

    # On a 401 the session token may have expired between the expiry check and
    # the request. Force a refresh and retry once. (A manual override has no
    # refresh token, so force_refresh returns None and we surface the 401.)
    if r.status_code == 401:
        new_token = auth.force_refresh()
        if new_token and new_token != token:
            r = _send(host, new_token, method, path, json_body, params)

    if r.status_code >= 400:
        raise APIError(r.status_code, r.text, str(r.request.url))
    if not r.content:
        return None
    try:
        return r.json()
    except Exception:
        return r.text
