#!/usr/bin/env bash
# Sign in to nrev-workflows once. The MCP server then refreshes the Supabase
# session automatically, so you never paste a JWT again.
#
# Usage:
#   bin/login.sh                 # production (default)
#   bin/login.sh --staging       # staging environment
#   bin/login.sh status          # show current session (any auth subcommand)
#
# Mirrors run-mcp.sh: prefers the repo checkout, falls back to the bundled copy.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SERVER="$PLUGIN_ROOT/../../servers/workflows"

if [ -f "$REPO_SERVER/pyproject.toml" ]; then
  TARGET="$REPO_SERVER"
else
  TARGET="$PLUGIN_ROOT/mcp"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "nrev-workflows: 'uv' not found. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

# Default to `login`; if the first arg is a known subcommand, use it as-is.
case "${1:-}" in
  login|logout|status) SUBCMD="$1"; shift ;;
  *) SUBCMD="login" ;;
esac

exec uv run --quiet --project "$TARGET" nrev-workflows auth "$SUBCMD" "$@"
