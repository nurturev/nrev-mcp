"""Tests for the shared transport's client-source attribution header.

The MCP server tags every backend call with ``X-Nrev-Client`` so prod-alert
triage attributes its traffic deterministically, instead of the backend
guessing ``nrev-lite`` from the python-httpx user-agent. These cases lock the
header onto the single choke point (``_send``) that ``api.py`` / ``tables_api.py``
/ ``um_api.py`` all route through, and confirm the existing auth/content headers
are preserved. Network is mocked — no live calls.
"""
from nrev_workflows_mcp import transport


class _FakeResponse:
    status_code = 200
    content = b""

    @property
    def request(self):  # only read on the error path; present for completeness
        return type("R", (), {"url": "http://test/"})()


class _FakeClient:
    """Stand-in for ``httpx.Client`` that records the headers it's built with."""

    last_headers: dict | None = None

    def __init__(self, *, base_url, headers, timeout):
        type(self).last_headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, path, json=None, params=None):
        return _FakeResponse()


def test_client_source_constant():
    assert transport.CLIENT_SOURCE == "nrev-mcp"


def test_send_includes_client_source_header(monkeypatch):
    monkeypatch.setattr(transport.httpx, "Client", _FakeClient)

    transport._send("http://host", "tok", "GET", "/x", None, None)

    assert _FakeClient.last_headers["X-Nrev-Client"] == "nrev-mcp"
    # Existing headers must be preserved (additive change only).
    assert _FakeClient.last_headers["Authorization"] == "Bearer tok"
    assert _FakeClient.last_headers["Content-Type"] == "application/json"


def test_request_entrypoint_carries_header(monkeypatch):
    # The public request() — used by api.py / tables_api.py / um_api.py — goes
    # through _send, so the header rides on every backend call uniformly.
    monkeypatch.setattr(transport.httpx, "Client", _FakeClient)
    monkeypatch.setattr(transport.auth, "get_jwt", lambda: "tok")

    transport.request("http://host", "GET", "/x")

    assert _FakeClient.last_headers["X-Nrev-Client"] == "nrev-mcp"
