#!/usr/bin/env bash
# Build what the one-line installer downloads.
#
# The repository is private, so `curl … | sh` cannot clone it. What it can do is
# fetch a tarball from the same host that serves the site, which is this file's
# job: pack the part of the tree the installer actually reads, next to a sha256
# the script checks before unpacking anything.
#
# `adapters/` and `service/` travel. The first is the plugin and the installer;
# the second is there because the wizard offers to bring a guard up locally, and
# that path runs `docker compose up --build` against `service/docker-compose.yml`.
# Shipping without it meant the offer was made and then failed on a missing file
# -- which is what happened on the first real install by someone else.
#
# The benchmark and the site do not travel: neither is needed to gate a harness.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$HERE/site"

# The Codex plugin vendors core into its marketplace tree, and that tree is a
# build artifact kept out of git. Packing without it produces an archive that
# installs and then silently skips the gate on Codex.
bash "$ROOT/adapters/packages/plugin-codex/sync-core.sh" >/dev/null

cd "$ROOT"
# `tar` does not read .gitignore, so every secret is excluded by name here. An
# archive built from a checkout that has `service/.env` would otherwise publish
# a working guard token and an LLM key on a public site.
tar --exclude='node_modules' --exclude='.DS_Store' --exclude='*.map' \
    --exclude='.env' --exclude='*.env' --exclude='.venv' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='runs' \
    -czf "$OUT/openmagi-src.tar.gz" adapters service
shasum -a 256 "$OUT/openmagi-src.tar.gz" | cut -d' ' -f1 > "$OUT/openmagi-src.tar.gz.sha256"

printf 'собрано: %s (%s)\n' "$OUT/openmagi-src.tar.gz" "$(du -h "$OUT/openmagi-src.tar.gz" | cut -f1)"
printf 'sha256:  %s\n' "$(cat "$OUT/openmagi-src.tar.gz.sha256")"
