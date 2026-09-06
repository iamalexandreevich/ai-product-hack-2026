#!/bin/sh
# OPENMAGI — one line to install.
#
#   curl -fsSL https://openmagi.ru/install.sh | sh
#   curl -fsSL https://openmagi.ru/install.sh | sh -s -- --level high --only kilo
#
# POSIX sh on purpose: this runs before anything of ours exists on the machine,
# so it may not assume bash, and every check below is a thing that has actually
# gone wrong rather than a thing that might.
set -eu

BASE="${OPENMAGI_BASE:-https://openmagi.ru}"
HOME_DIR="${OPENMAGI_HOME:-$HOME/.local/share/openmagi}"
SRC="$HOME_DIR/src"

say()  { printf '%s\n' "$*"; }
die()  { printf '%s\n' "openmagi: $*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "$1 is required and was not found"; }
need curl
need tar
need node

# The installer reads TypeScript directly, which node only strips from 22.6 on.
# An older node fails deep inside an import with a syntax error that reads like
# our bug rather than a version problem, so the version is checked here where
# the message can say what to do.
node -e 'const [a,b]=process.versions.node.split(".").map(Number)
if (a<22 || (a===22 && b<6)) { console.error(`openmagi: node ${process.versions.node} is too old; 22.6 or newer is required`); process.exit(1) }'

# Bundling the harness plugin is a bun job. Without it the install still runs and
# still gates, but through a slower path -- so this is a warning, not a stop.
# Not installed for you: bun's own installer is another `curl … | sh`, and
# chaining one into another behind the user's back is exactly the shape this
# product exists to refuse.
if ! command -v bun >/dev/null 2>&1; then
  say "openmagi: bun not found — plugin bundles will be skipped."
  say "          install it first for the full path: https://bun.sh"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

say "→ downloading openmagi"
curl -fsSL "$BASE/openmagi-src.tar.gz"        -o "$TMP/src.tar.gz"
curl -fsSL "$BASE/openmagi-src.tar.gz.sha256" -o "$TMP/src.sha256"

# Verified before anything is unpacked, let alone run. A tarball fetched over a
# hijacked connection would otherwise be executed as the user who ran this.
WANT="$(cat "$TMP/src.sha256")"
if command -v shasum >/dev/null 2>&1; then GOT="$(shasum -a 256 "$TMP/src.tar.gz" | cut -d' ' -f1)"
elif command -v sha256sum >/dev/null 2>&1; then GOT="$(sha256sum "$TMP/src.tar.gz" | cut -d' ' -f1)"
else die "no sha256 tool found; refusing to unpack an archive I cannot verify"
fi
[ "$WANT" = "$GOT" ] || die "checksum mismatch — expected $WANT, got $GOT"

say "→ unpacking"
rm -rf "$SRC.new"
mkdir -p "$SRC.new"
tar -xzf "$TMP/src.tar.gz" -C "$SRC.new"
# Swapped in one move: an interrupted unpack must not leave a half-tree that the
# next run mistakes for a finished one.
rm -rf "$SRC.old"
[ -d "$SRC" ] && mv "$SRC" "$SRC.old"
mv "$SRC.new" "$SRC"
rm -rf "$SRC.old"

ENTRY="$SRC/adapters/packages/installer/bin/openmagi.js"
[ -f "$ENTRY" ] || die "archive did not contain the installer"

# Piped into `sh`, this script's stdin is the pipe -- already spent. The wizard
# would read EOF and fall through to defaults without asking anything. Handing
# it the terminal directly is what makes `curl … | sh` interactive at all; with
# no terminal (CI, a container) it stays non-interactive and answers from flags.
# Tried rather than tested: `[ -r /dev/tty ]` is true in a container with no
# controlling terminal, and the redirect then fails with "Device not configured"
# after the download and unpack have already happened. Opening it is the only
# question worth asking.
if { : < /dev/tty; } 2>/dev/null; then
  exec node "$ENTRY" install "$@" < /dev/tty
else
  say "openmagi: no terminal — installing with defaults; pass flags to choose"
  exec node "$ENTRY" install "$@"
fi
