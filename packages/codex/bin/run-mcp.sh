#!/usr/bin/env bash
# Launch the nrev-workflows MCP server (stdio) for Codex.
#
# Codex spawns MCP servers outside its sandbox but with a minimal environment:
# only PATH, HOME, and a small whitelist are forwarded, and the desktop app's
# own PATH may not include uv's install dir — so we prepend the common uv
# locations before resolving it.
#
# Referenced from .mcp.json by a relative path + `cwd` (Codex resolves both
# against the plugin root; it does NOT expand ${CLAUDE_PLUGIN_ROOT}-style
# variables in MCP configs).
#
# Two layouts supported:
#   1. Repo checkout: the server source lives at <repo>/servers/workflows —
#      preferred during development (single source of truth).
#   2. Plugin install: only this package is present, so the server is bundled
#      at ./mcp (kept in sync by scripts/sync-agents.sh).
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

PKG_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SERVER="$PKG_ROOT/../../servers/workflows"

if [ -f "$REPO_SERVER/pyproject.toml" ]; then
  TARGET="$REPO_SERVER"
else
  TARGET="$PKG_ROOT/mcp"
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "nrev-workflows MCP: 'uv' not found. Install it with:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

exec uv run --quiet --project "$TARGET" nrev-workflows-mcp
