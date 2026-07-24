#!/usr/bin/env bash
# Launch the nrev-workflows MCP server (stdio), for local development.
#
# Two layouts supported:
#   1. Repo checkout: the server source lives at <repo>/servers/workflows —
#      preferred during development (single source of truth).
#   2. Bundled copy: packages/claude/mcp (kept in sync by scripts/sync-agents.sh),
#      used if servers/workflows isn't present.
#
# Requires `uv` (https://docs.astral.sh/uv/) — it resolves dependencies on
# first run; no manual pip install.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SERVER="$REPO_ROOT/servers/workflows"

if [ -f "$REPO_SERVER/pyproject.toml" ]; then
  TARGET="$REPO_SERVER"
else
  TARGET="$REPO_ROOT/packages/claude/mcp"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "nrev-workflows MCP: 'uv' not found. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

exec uv run --quiet --project "$TARGET" nrev-workflows-mcp
