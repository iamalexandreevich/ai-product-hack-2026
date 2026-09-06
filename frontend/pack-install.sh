#!/usr/bin/env bash
# Build what the one-line installer downloads.
#
# The repository is private, so `curl … | sh` cannot clone it. What it can do is
# fetch a tarball from the same host that serves the site, which is this file's
# job: pack the part of the tree the installer actually reads, next to a sha256
# the script checks before unpacking anything.
#
# Only `adapters/` travels. The service, the benchmark and the site itself are
# not needed to install a plugin into a harness, and shipping them would mean a
# download five times the size for nothing.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$HERE/site"

# The Codex plugin vendors core into its marketplace tree, and that tree is a
# build artifact kept out of git. Packing without it produces an archive that
# installs and then silently skips the gate on Codex.
bash "$ROOT/adapters/packages/plugin-codex/sync-core.sh" >/dev/null

cd "$ROOT"
tar --exclude='node_modules' --exclude='.DS_Store' --exclude='*.map' \
    -czf "$OUT/openmagi-src.tar.gz" adapters
shasum -a 256 "$OUT/openmagi-src.tar.gz" | cut -d' ' -f1 > "$OUT/openmagi-src.tar.gz.sha256"

printf 'собрано: %s (%s)\n' "$OUT/openmagi-src.tar.gz" "$(du -h "$OUT/openmagi-src.tar.gz" | cut -f1)"
printf 'sha256:  %s\n' "$(cat "$OUT/openmagi-src.tar.gz.sha256")"
