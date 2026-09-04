#!/usr/bin/env bash
# Codex copies a plugin into ~/.codex/plugins/cache/, so the plugin has to be
# self-contained: vendor @agentgate/gate-core next to the hook scripts.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="$here/marketplace/plugins/gate/vendor/core"
rm -rf "$dest"; mkdir -p "$dest"
cp "$here/../core/src/"*.ts "$dest/"
echo "vendored core -> ${dest#"$here/"}"
