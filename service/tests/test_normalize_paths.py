import os

from agentgate.normalize.paths import is_within, looks_like_path, matches_any, resolve_path


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
