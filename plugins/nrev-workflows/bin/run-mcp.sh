#!/usr/bin/env bash
# Launch the nrev-workflows MCP server (stdio).
#
# Two layouts supported:
#   1. Repo checkout: the server source lives at <repo>/servers/workflows —
#      preferred during development (single source of truth).
#   2. Marketplace install: only the plugin directory is present, so the
#      server is bundled at <plugin>/mcp (kept in sync by scripts/sync-plugin.sh).
#
# Requires `uv` (https://docs.astral.sh/uv/) — it resolves dependencies on
# first run; no manual pip install.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SERVER="$PLUGIN_ROOT/../../servers/workflows"

if [ -f "$REPO_SERVER/pyproject.toml" ]; then
  TARGET="$REPO_SERVER"
else
  TARGET="$PLUGIN_ROOT/mcp"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "nrev-workflows MCP: 'uv' not found. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

exec uv run --quiet --project "$TARGET" nrev-workflows-mcp
