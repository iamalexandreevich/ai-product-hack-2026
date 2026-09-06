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
# GATE_SKIP_PATCH builds the untouched release, for A/B-ing whether a problem
# belongs to the patch or to the way we build.
if [ "${GATE_SKIP_PATCH:-}" = "1" ]; then
  echo "→ SKIPPING patches (GATE_SKIP_PATCH=1)"
else
  git am "$PATCHDIR"/*.patch
fi

# The bun runtime is compiled into the binary, so a build made with a different
# bun is a different program: it passes the smoke tests, runs headless and even
# renders under tmux, and then never paints its TUI in a real terminal. The
# upstream check is deliberately relaxed to a caret range and lets that through,
# so pin it here against what the repo itself declares.
BUN_BIN="${GATE_BUN:-$(command -v bun || true)}"
[ -n "$BUN_BIN" ] || { echo "✗ bun not found; install it or set GATE_BUN"; exit 1; }
BUN_HAVE="$("$BUN_BIN" --version)"
BUN_WANT="$(node -p "(require('./package.json').packageManager||'').replace(/^bun@/,'')" 2>/dev/null || true)"

if [ -n "$BUN_WANT" ] && [ "$BUN_WANT" != "$BUN_HAVE" ]; then
  cat >&2 <<MSG
✗ bun version mismatch
    this release pins: bun@$BUN_WANT   (package.json → packageManager)
    you are building with: bun $BUN_HAVE   ($BUN_BIN)

  bun compiles its runtime into the binary. Building with another version yields
  a program that passes every smoke test, works headless and under tmux — and
  never draws its TUI in a real terminal. That is not a hypothetical.

  Fix:
    npm i bun@$BUN_WANT --prefix /tmp/gate-bun
    GATE_BUN=/tmp/gate-bun/node_modules/.bin/bun $0 $HARNESS $REPO $TAG
MSG
  exit 1
fi
[ -n "$BUN_WANT" ] || echo "  (repo pins no bun version; building with $BUN_HAVE)"

# Everything from here uses the pinned bun — the lockfile must be resolved by
# the same version that compiles the binary.
STUB="$WORK/stub-bin"
mkdir -p "$STUB"
ln -sf "$BUN_BIN" "$STUB/bun"
printf '#!/bin/sh\nexit 0\n' > "$STUB/gh"
chmod +x "$STUB/gh"
export PATH="$STUB:$PATH"

echo "→ bun install (bun $BUN_HAVE)"
bun install

echo "→ building ($PLATFORM)"
cd packages/opencode
# The version and release mode come from the environment, not from the git tag.
# Without them the build stamps itself 0.0.0--<date>, keeps sourcemaps, and picks a
# different data directory — i.e. it behaves unlike the release it is patching.
VERSION="${TAG#v}"
PREFIX="$(echo "$HARNESS" | tr '[:lower:]' '[:upper:]')"
echo "→ building as $PREFIX version $VERSION (release mode)"
# Release mode drops sourcemaps and stamps BUILD_KIND=release, but it also makes
# the upstream script publish a GitHub release at the end; `gh` is stubbed above
# so that step succeeds locally without touching anything remote.
#
# Release mode ends by publishing: it archives each built platform and then runs
# `gh release upload ./dist/*.zip ./dist/*.tar.gz`. On macOS only the .zip is
# ever created, so bun fails expanding the .tar.gz glob -- before `gh` is
# reached, which is why stubbing `gh` does not help. The binary is finished by
# then; only the upload is not. So the step is allowed to fail and the check
# below decides: no binary means a real failure, a binary means we have what we
# came for.
env "${PREFIX}_VERSION=$VERSION" "${PREFIX}_RELEASE=true" \
  bun run script/build.ts --single \
  || echo "  (upstream publish step failed; looking for the binary it built)"

BIN="$(find dist -type f -name "$HARNESS" -o -type f -name "${HARNESS}-*" 2>/dev/null | grep -v '\.' | head -1)"
[ -z "$BIN" ] && BIN="$(find dist -type f -perm -u+x | head -1)"
[ -z "$BIN" ] && { echo "✗ no binary produced"; exit 1; }

# The binary is not alone: the release ships tree-sitter wasm and worker scripts
# beside it and loads them by relative path. Copying just the executable produces
# an install that starts but misbehaves, so take the whole directory.
mkdir -p "$OUT"
cp -R "$(dirname "$BIN")"/. "$OUT/"
mv "$OUT/$(basename "$BIN")" "$OUT/$HARNESS" 2>/dev/null || cp "$BIN" "$OUT/$HARNESS"
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
