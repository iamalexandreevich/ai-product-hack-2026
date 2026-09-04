#!/usr/bin/env bash
# Build a gate-patched harness binary pinned to an exact upstream tag.
#
#   ./build.sh <harness> <repo-url> <tag>
#   ./build.sh opencode https://github.com/anomalyco/opencode.git v1.17.18
#
# The binary MUST match the user's installed version exactly: opencode/kilo keep
# sessions and credentials in a shared SQLite database, and a different version
# would migrate its schema and break the original install. See the plan, D7.
#
# Requires: bun, git. Builds only the current platform (--single).
set -euo pipefail

HARNESS="${1:?usage: build.sh <harness> <repo-url> <tag>}"
REPO="${2:?repo url required}"
TAG="${3:?tag required}"

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
OUT="${GATE_CATALOG_DIR:-$HOME/.local/share/gate-catalog}/$HARNESS/${TAG#v}"
PLATFORM="$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m | sed 's/aarch64/arm64/;s/x86_64/x64/')"

echo "→ cloning $REPO @ $TAG"
git clone --filter=blob:none --depth 1 -b "$TAG" "$REPO" "$WORK/src"
cd "$WORK/src"
git config user.email gate@agentgate.dev
git config user.name AgentGate

echo "→ applying gate patches"
PATCHDIR="$HERE/patches/$HARNESS"
[ -d "$PATCHDIR" ] || { echo "✗ no patches for $HARNESS (expected $PATCHDIR)"; exit 1; }
git am "$PATCHDIR"/*.patch

echo "→ bun install"
bun install

echo "→ building ($PLATFORM)"
cd packages/opencode
bun run script/build.ts --single --skip-embed-web-ui

BIN="$(find dist -type f -name "$HARNESS" -o -type f -name "${HARNESS}-*" 2>/dev/null | grep -v '\.' | head -1)"
[ -z "$BIN" ] && BIN="$(find dist -type f -perm -u+x | head -1)"
[ -z "$BIN" ] && { echo "✗ no binary produced"; exit 1; }

mkdir -p "$OUT"
cp "$BIN" "$OUT/$HARNESS"
chmod +x "$OUT/$HARNESS"
SHA="$(shasum -a 256 "$OUT/$HARNESS" | cut -d' ' -f1)"
echo "✓ $OUT/$HARNESS"
echo "  sha256: $SHA"
echo "  platform: $PLATFORM  tag: $TAG"

# Update the manifest so the installer can find this exact build.
python3 - "$HERE/manifest.json" "$HARNESS" "${TAG#v}" "$PLATFORM" "$OUT/$HARNESS" "$SHA" <<'PY'
import json, sys, pathlib
mf, harness, tag, platform, path, sha = sys.argv[1:7]
p = pathlib.Path(mf)
data = json.loads(p.read_text()) if p.exists() else {}
data.setdefault(harness, {}).setdefault(tag, {})[platform] = {"url": f"file://{path}", "sha256": sha}
p.write_text(json.dumps(data, indent=2) + "\n")
print(f"  manifest updated: {mf}")
PY
