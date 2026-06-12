#!/usr/bin/env bash
# Set the plugin version in every place that carries one, in lockstep, so they
# can never drift. Run before tagging a release.
#
#   scripts/bump-version.sh 0.3.0
#
# Authoritative for Claude Code update detection is plugin.json's `version`
# (it wins over the marketplace entry; /plugin update does a semver compare).
# Claude Cowork tracks the git commit, not the field, so a fresh commit/tag is
# what it syncs. The MCP server's pyproject version is invisible to Claude Code
# — we keep it aligned only for tidiness / eventual PyPI publish.
#
# Files updated:
#   plugins/nrev-workflows/.claude-plugin/plugin.json   (AUTHORITATIVE)
#   .claude-plugin/marketplace.json                     (catalog entry, kept in sync)
#   servers/workflows/pyproject.toml                    (MCP package, independent of CC)
#   plugins/nrev-workflows/mcp/pyproject.toml           (propagated by sync-plugin.sh)
set -euo pipefail

VERSION="${1:-}"
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: $(basename "$0") MAJOR.MINOR.PATCH   (strict semver, no leading 'v')" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_JSON="$REPO_ROOT/plugins/nrev-workflows/.claude-plugin/plugin.json"
MARKET_JSON="$REPO_ROOT/.claude-plugin/marketplace.json"
SERVER_PYPROJECT="$REPO_ROOT/servers/workflows/pyproject.toml"

# Surgical regex edits — change ONLY the version string, leaving each file's
# formatting untouched (no JSON reserialization, no diff noise). Both JSON files
# carry exactly one `"version"` key for the single nrev-workflows plugin; the
# pyproject carries one `version =` line under [project].
python3 - "$VERSION" "$PLUGIN_JSON" "$MARKET_JSON" "$SERVER_PYPROJECT" <<'PY'
import re, sys
version, plugin_path, market_path, pyproject_path = sys.argv[1:5]

def sub(path, pattern, repl):
    text = open(path).read()
    new, n = re.subn(pattern, repl, text, count=1)
    if n != 1:
        raise SystemExit(f"expected exactly one version match in {path}, found {n}")
    open(path, "w").write(new)

sub(plugin_path, r'("version":\s*")[^"]*(")', rf'\g<1>{version}\g<2>')
sub(market_path, r'("version":\s*")[^"]*(")', rf'\g<1>{version}\g<2>')
sub(pyproject_path, r'(?m)^(version = ")[^"]*(")', rf'\g<1>{version}\g<2>')
PY

# Propagate the bundled MCP copy (includes its pyproject.toml).
bash "$REPO_ROOT/scripts/sync-plugin.sh"

echo "bumped to $VERSION across plugin.json, marketplace.json, and pyproject (bundle re-synced)"
echo "next: update CHANGELOG.md, commit, then: git tag v$VERSION"
