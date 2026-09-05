import pytest
from agentgate.normalize.domains import extract_domains


def test_urls():
    assert extract_domains(["curl", "https://Evil.sh/x.sh"]) == ["evil.sh"]
    assert extract_domains(["wget", "-q", "http://a.b:8080/p"]) == ["a.b"]


def test_git_and_ssh_forms():
    assert extract_domains(["git", "clone", "git@github.com:org/repo.git"]) == ["github.com"]
    assert extract_domains(["ssh", "root@10.0.0.5", "id"]) == ["10.0.0.5"]
    assert extract_domains(["scp", "f", "u@host.example:/tmp/"]) == ["host.example"]


def test_dedup_and_none():
    assert extract_domains(["curl", "http://x", "http://x/y"]) == ["x"]
    assert extract_domains(["ls", "-la"]) == []


# --- Fix round 1: Critical 1 — a malformed URL must not raise ---


def test_malformed_ipv6_url_does_not_raise():
    assert extract_domains(["curl", "http://[evil"]) == []
    # a well-formed token later in argv is still extracted
    assert extract_domains(["curl", "http://[evil", "http://ok.example"]) == ["ok.example"]


@pytest.mark.parametrize(
    "argv",
    [
        ["curl", "-H", "Accept: text/html", "https://pypi.org/x"],
        ["echo", "note: see /tmp"],
        ["git", "commit", "-m", "fix: handle a/b"],
    ],
    ids=["header_value", "prose_with_colon", "commit_message"],
)
def test_a_token_with_whitespace_is_never_an_scp_remote(argv):
    assert "accept" not in extract_domains(argv)
    assert "note" not in extract_domains(argv)
    assert "fix" not in extract_domains(argv)


def test_a_real_scp_remote_still_counts():
    assert extract_domains(["scp", "file", "host.example:/tmp/x"]) == ["host.example"]
