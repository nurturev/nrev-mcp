"""Command line: ``nrev-workflows auth login|logout|status``.

Run ``login`` once to sign in via the browser; the MCP server then refreshes the
Supabase session automatically, so you never paste a JWT again. Choose the
environment with ``--staging`` / ``--prod`` (default prod) or ``NREV_ENV``.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from . import auth, config
from . import login as login_mod


def _apply_env(args: argparse.Namespace) -> None:
    if getattr(args, "staging", False):
        os.environ["NREV_ENV"] = "staging"
    elif getattr(args, "prod", False):
        os.environ["NREV_ENV"] = "prod"


def _cmd_login(args: argparse.Namespace) -> int:
    _apply_env(args)
    print(f"Signing in to nrev-workflows ({config.env_name()} → {config.webapp_url()})")

    def on_url(url: str) -> None:
        print("Opening your browser to sign in with Google…")
        print(f"If it doesn't open, visit:\n  {url}\n")

    try:
        st = login_mod.login(on_url=on_url)
    except login_mod.LoginError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    email = st.get("email") or "unknown"
    tenant = st.get("tenant")
    tenant_str = f", tenant: {tenant}" if tenant is not None else ""
    print(f"\n✓ Logged in as {email}{tenant_str} (env: {st.get('env')})")
    print(
        f"Session saved to {config.credentials_file()} — the MCP server will "
        f"refresh it automatically."
    )
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    auth.clear_credentials()
    print("Logged out — credentials cleared.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    _apply_env(args)
    st = auth.status()
    if st.get("status") != "set":
        print("Not logged in. Run: nrev-workflows auth login")
        return 0

    print(f"Source:  {st.get('source')}")
    if st.get("email"):
        print(f"Email:   {st['email']}")
    if st.get("tenant") is not None:
        print(f"Tenant:  {st['tenant']}")
    if st.get("env"):
        print(f"Env:     {st['env']}")
    mins = st.get("expires_in_minutes")
    if st.get("expired"):
        print("Token:   expired (refreshes on next request)")
    elif mins is not None:
        print(f"Token:   valid ({mins}m remaining)")
    if st.get("env_mismatch"):
        print(f"⚠ {st['env_mismatch']}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nrev-workflows",
        description="nRev workflows MCP — session management.",
    )
    groups = parser.add_subparsers(dest="group", required=True)
    auth_parser = groups.add_parser("auth", help="Manage authentication.")
    auth_sub = auth_parser.add_subparsers(dest="cmd", required=True)

    for name, fn, helptext in (
        ("login", _cmd_login, "Sign in via the browser (once)."),
        ("logout", _cmd_logout, "Clear the stored session."),
        ("status", _cmd_status, "Show the current session."),
    ):
        sp = auth_sub.add_parser(name, help=helptext)
        sp.add_argument("--staging", action="store_true", help="Use the staging environment.")
        sp.add_argument("--prod", action="store_true", help="Use production (default).")
        sp.set_defaults(func=fn)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
