#!/usr/bin/env bash
# Fan the single source of truth (shared/skills + shared/AGENTS.md) and the MCP
# server bundle out to every agent package, so one edit reaches Claude Code,
# Codex CLI, and Gemini CLI. Every agent is treated identically.
#
#   scripts/sync-agents.sh            # --build (default): regenerate all packages
#   scripts/sync-agents.sh --link-dev # symlink shared/skills into each agent's
#                                      # live skills dir for local development
#
# Canonical (edit here):
#   shared/skills/        the 10 SKILL.md dirs (open Agent Skills format)
#   shared/AGENTS.md      always-on context (thin — the server ships INSTRUCTIONS)
#
# Generated under packages/<agent>/ (never hand-edit — committed so installers
# can fetch them):
#   packages/claude/   skills/ + mcp/
#   packages/codex/    skills/ + mcp/ + AGENTS.md
#   packages/gemini/   skills/ + mcp/ + GEMINI.md
# Each package also carries a hand-authored manifest (.claude-plugin/ /
# plugin.json / config.toml / gemini-extension.json) that is NOT generated.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHARED_SKILLS="$REPO_ROOT/shared/skills"
SHARED_AGENTS="$REPO_ROOT/shared/AGENTS.md"
SERVER_SRC="$REPO_ROOT/servers/workflows"
PKGS="$REPO_ROOT/packages"

# Copy the canonical skills into a package's skills/ dir (full replace).
copy_skills() {
  local dest="$1"
  rm -rf "$dest"; mkdir -p "$dest"
  cp -R "$SHARED_SKILLS"/. "$dest"/
}

# Bundle the MCP server source into a package so the install is self-contained
# (marketplace/extension installs only get the package dir, not servers/).
bundle_server() {
  local dest="$1"
  rm -rf "$dest"; mkdir -p "$dest"
  cp -R "$SERVER_SRC/src" "$dest/src"
  cp "$SERVER_SRC/pyproject.toml" "$dest/pyproject.toml"
  [ -f "$SERVER_SRC/uv.lock" ] && cp "$SERVER_SRC/uv.lock" "$dest/uv.lock"
}

build() {
  for agent in claude codex gemini; do
    copy_skills   "$PKGS/$agent/skills"
    bundle_server "$PKGS/$agent/mcp"
  done
  # Per-agent always-on context file — naming differs by agent:
  #   Codex reads AGENTS.md natively; Gemini reads its contextFileName (GEMINI.md).
  #   Claude relies on the server INSTRUCTIONS + skills, so it ships no context file.
  cp "$SHARED_AGENTS" "$PKGS/codex/AGENTS.md"
  cp "$SHARED_AGENTS" "$PKGS/gemini/GEMINI.md"
  echo "synced shared/ -> packages/{claude,codex,gemini}"
}

# Symlink the canonical skills into each agent's live user-scoped skills dir so
# local edits under shared/ are picked up without a rebuild. Per-skill symlinks
# so we never clobber an existing skills directory.
link_dev() {
  local targets=(
    "$HOME/.claude/skills"   # Claude Code
    "$HOME/.agents/skills"   # Codex CLI
    "$HOME/.gemini/skills"   # Gemini CLI
  )
  for base in "${targets[@]}"; do
    mkdir -p "$base"
    for skill in "$SHARED_SKILLS"/*/; do
      ln -sfn "${skill%/}" "$base/$(basename "$skill")"
    done
    echo "linked $(ls -1 "$SHARED_SKILLS" | wc -l | tr -d ' ') skills -> $base"
  done
}

case "${1:---build}" in
  --build)    build ;;
  --link-dev) link_dev ;;
  *) echo "usage: $(basename "$0") [--build|--link-dev]" >&2; exit 1 ;;
esac
