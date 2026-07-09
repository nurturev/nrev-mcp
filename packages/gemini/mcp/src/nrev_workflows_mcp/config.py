"""Environment, host, and credential-path resolution.

Auth is platform-mediated (relay): the user signs in via the web app, which
holds the Supabase session; that session is handed to the CLI through the
``/cli/auth/done`` page, and refreshed via user-management. The MCP itself never
talks to Supabase and embeds no keys — login points at the web app, refresh at
user-management, and API calls at the workflow/tables hosts. All four are the
same environment.

Pick the environment with ``NREV_ENV=prod|staging`` (default ``prod``). Any host
can be overridden individually (``NREV_WEBAPP_URL`` / ``NREV_UM_URL`` /
``NREV_WF_HOST`` / ``NREV_TABLES_HOST``), which wins over ``NREV_ENV``.
Credentials live under ``~/.nrev-workflows`` (override with ``NREV_WORKFLOWS_DIR``).
"""
from __future__ import annotations

import os
from pathlib import Path

_ENVS = {
    "prod": {
        "webapp": "https://app.nrev.ai",
        "um": "https://umws.public.prod.nurturev.com",
        "workflow": "https://workflow.public.prod.nurturev.com",
        "tables": "https://nrev-tables-service.public.prod.nurturev.com",
    },
    "staging": {
        "webapp": "https://app.staging.nrev.ai",
        "um": "https://umws.public.staging.nurturev.com",
        "workflow": "https://workflow.public.staging.nurturev.com",
        "tables": "https://nrev-tables-service.public.staging.nurturev.com",
    },
}

DEFAULT_ENV = "prod"


def _session_env() -> "str | None":
    """The environment recorded in the persisted session, if any.

    Lets the MCP server follow the env you logged into (``auth login --staging``)
    without also having to set NREV_ENV on the server. Read directly off disk to
    avoid importing the auth module (which imports this one).
    """
    try:
        import json

        path = credentials_file()
        if path.exists():
            return (json.loads(path.read_text()) or {}).get("env")
    except Exception:
        return None
    return None


def env_name() -> str:
    """Resolve the active environment.

    Precedence: an explicit ``NREV_ENV`` wins; else the logged-in session's env
    (so the server follows your login); else the default (prod).
    """
    explicit = os.environ.get("NREV_ENV", "").strip().lower()
    if explicit in _ENVS:
        return explicit
    session = _session_env()
    if session in _ENVS:
        return session
    return DEFAULT_ENV


def _host(key: str, override_var: str) -> str:
    return (os.environ.get(override_var) or _ENVS[env_name()][key]).rstrip("/")


def webapp_url() -> str:
    """Web app base — where the user signs in (login is relayed from here)."""
    return _host("webapp", "NREV_WEBAPP_URL")


def um_url() -> str:
    """user-management base — where the session token is refreshed."""
    return _host("um", "NREV_UM_URL")


def workflow_host() -> str:
    return _host("workflow", "NREV_WF_HOST")


def tables_host() -> str:
    return _host("tables", "NREV_TABLES_HOST")


def config_dir() -> Path:
    return Path(
        os.environ.get("NREV_WORKFLOWS_DIR") or (Path.home() / ".nrev-workflows")
    )


def credentials_file() -> Path:
    return config_dir() / "credentials"
