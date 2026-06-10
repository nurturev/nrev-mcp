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


def request(
    host: str,
    method: str,
    path: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
) -> Any:
    with httpx.Client(
        base_url=host,
        headers={
            "Authorization": f"Bearer {auth.get_jwt()}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(TIMEOUT_SECONDS),
    ) as client:
        r = client.request(method, path, json=json_body, params=params)
        if r.status_code >= 400:
            raise APIError(r.status_code, r.text, str(r.request.url))
        if not r.content:
            return None
        try:
            return r.json()
        except Exception:
            return r.text
