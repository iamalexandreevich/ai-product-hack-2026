import os

from agentgate.normalize.paths import (
    is_within,
    looks_like_path,
    looks_unresolved,
    matches_any,
    resolve_path,
)


def test_resolve_relative_and_home():
    assert resolve_path("./dist", "/home/u/repo") == "/home/u/repo/dist"
    assert resolve_path("../x", "/home/u/repo") == "/home/u/x"
    assert resolve_path("~/.ssh/id_rsa", "/r") == os.path.expanduser("~/.ssh/id_rsa")
    assert resolve_path("/etc/passwd", "/r") == "/etc/passwd"
    assert resolve_path("src/*.py", "/r") == "/r/src/*.py"


def test_looks_like_path():
    assert looks_like_path("./a")
    assert looks_like_path("../a")
    assert looks_like_path("/a")
    assert looks_like_path("~/a")
    assert looks_like_path("src/main.py")
    assert not looks_like_path("http://x/y")
    assert not looks_like_path("-rf")
    assert not looks_like_path("install")


def test_is_within():
    assert is_within("/r/a/b", ["/r"])
    assert is_within("/r", ["/r"])
    assert not is_within("/rx/a", ["/r"])
    assert not is_within("/etc/passwd", ["/r", "/tmp/s"])


def test_matches_any_basename_and_relative():
    ws = "/r"
    assert matches_any("/r/.env", [".env*"], ws)
    assert matches_any("/r/.env.local", [".env*"], ws)
    assert matches_any("/r/.git/hooks/pre-commit", [".git/hooks/**"], ws)
    assert matches_any("/r/sub/.claude/settings.json", [".claude/**"], ws)
    assert matches_any(os.path.expanduser("~/.ssh/authorized_keys"), ["~/.ssh/**"], ws)
    assert matches_any("/r/AGENTS.md", ["AGENTS.md"], ws)
    assert not matches_any("/r/src/app.py", [".env*", ".git/hooks/**"], ws)
    assert matches_any("/r/certs/server.pem", ["*.pem"], ws)


# --- Fix round 1: Important 5 — matches_any must be case-insensitive ---


def test_matches_any_is_case_insensitive():
    ws = "/r"
    assert matches_any("/r/.ENV", [".env*"], ws)
    assert matches_any("/r/x.PEM", ["*.pem"], ws)
    assert matches_any("/r/.Git/Hooks/pre-commit", [".git/hooks/**"], ws)


# --- Fix round 1: Important 6 — ~user must not trigger a pwd/NSS lookup ---


def test_resolve_path_leaves_tilde_user_unexpanded():
    # Must not call os.path.expanduser (pwd/NSS lookup) for ~user forms;
    # the token is treated as a literal relative path component instead.
    result = resolve_path("~root/.ssh/id_rsa", "/r")
    assert result == "/r/~root/.ssh/id_rsa"
    assert not result.startswith("/var/root") and not result.startswith("/root")


def test_resolve_path_still_expands_bare_tilde_and_tilde_slash():
    assert resolve_path("~", "/r") == os.path.expanduser("~")
    assert resolve_path("~/x", "/r") == os.path.expanduser("~/x")


def test_looks_unresolved():
    assert looks_unresolved("~root/.ssh/id_rsa")
    assert looks_unresolved("$HOME/dist")
    assert looks_unresolved("file{a,b}.txt")
    assert not looks_unresolved("~/dist")
    assert not looks_unresolved("~")
    assert not looks_unresolved("/etc/passwd")
    assert not looks_unresolved("src/main.py")


# --- Fix round 1: Important 7 — a bare sensitive basename must be a path ---


def test_looks_like_path_flags_sensitive_bare_basenames():
    assert looks_like_path(".env")
    assert looks_like_path(".env.local")
    assert looks_like_path("id_rsa")
    assert looks_like_path("id_ed25519.pub")
    assert looks_like_path("server.pem")
    assert looks_like_path("service.key")
    assert looks_like_path("credentials")
    assert looks_like_path(".netrc")
    assert looks_like_path(".npmrc")
    assert looks_like_path(".git-credentials")
    # case-insensitive, consistent with matches_any (Important 5)
    assert looks_like_path(".ENV")
    assert looks_like_path("ID_RSA")
    # still False for an ordinary bare word that isn't a path or a
    # sensitive filename (e.g. a subcommand name)
    assert not looks_like_path("install")
