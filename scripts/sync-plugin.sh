#!/usr/bin/env bash
# Copy the server source into the plugin so marketplace installs (which only
# get the plugin directory) are self-contained. Run before tagging a release.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO_ROOT/servers/workflows"
DEST="$REPO_ROOT/plugins/nrev-workflows/mcp"

rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$SRC/src" "$DEST/src"
cp "$SRC/pyproject.toml" "$DEST/pyproject.toml"
if [ -f "$SRC/uv.lock" ]; then
  cp "$SRC/uv.lock" "$DEST/uv.lock"
fi

echo "synced $SRC -> $DEST"
