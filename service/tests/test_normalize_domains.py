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
