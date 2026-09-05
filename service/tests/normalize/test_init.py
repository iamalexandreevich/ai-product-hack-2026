from agentgate.api.schemas import ActionArgs, DecideRequest, McpArgs, Tool
from agentgate.normalize import normalize

CWD = "/home/u/repo"


def test_file_read_resolves_paths():
    req = DecideRequest(
        harness="h",
        tool=Tool.file_read,
        args=ActionArgs(cwd=CWD, paths=["./a.py", "/etc/passwd"]),
        user_request="x",
    )
    a = normalize(req)
    assert a.paths == ["/home/u/repo/a.py", "/etc/passwd"]
    assert a.commands == []
    assert a.domains == []


def test_file_write_resolves_paths():
    req = DecideRequest(
        harness="h",
        tool=Tool.file_write,
        args=ActionArgs(cwd=CWD, paths=["out.txt"]),
        user_request="x",
    )
    a = normalize(req)
    assert a.paths == ["/home/u/repo/out.txt"]


def test_network_lowercases_and_dedups_domains():
    req = DecideRequest(
        harness="h",
        tool=Tool.network,
        args=ActionArgs(cwd=CWD, domains=["Evil.COM", "x.io", "evil.com"]),
        user_request="x",
    )
    a = normalize(req)
    assert a.domains == sorted({"evil.com", "x.io"})
    assert a.paths == []


def test_mcp_call_carries_mcp_args_through():
    mcp = McpArgs(server="s", tool="t", arguments={"k": "v"})
    req = DecideRequest(
        harness="h",
        tool=Tool.mcp_call,
        args=ActionArgs(cwd=CWD, mcp=mcp),
        user_request="x",
    )
    a = normalize(req)
    assert a.mcp == mcp
    assert a.paths == []
    assert a.domains == []


def test_shell_delegates_to_normalize_shell():
    req = DecideRequest(
        harness="h",
        tool=Tool.shell,
        raw="ls -la",
        args=ActionArgs(cwd=CWD),
        user_request="x",
    )
    a = normalize(req)
    assert [c.argv for c in a.commands] == [["ls", "-la"]]


# --- Fix round 1: Important 6 — ~user must not be fabricated into a path here either ---


def test_file_read_tilde_user_path_is_flagged_not_fabricated():
    req = DecideRequest(
        harness="h",
        tool=Tool.file_read,
        args=ActionArgs(cwd=CWD, paths=["~root/.ssh/id_rsa", "./a.py"]),
        user_request="x",
    )
    a = normalize(req)
    assert a.flags.has_unresolved_expansion is True
    assert a.paths == ["/home/u/repo/a.py"]


def test_a_network_action_carries_its_method():
    action = normalize(DecideRequest.model_validate(
        {"harness": "t", "tool": "network", "args": {"cwd": "/w", "domains": ["GitHub.com"], "method": "HEAD"},
         "user_request": "x"}
    ))

    assert action.method == "HEAD"
    assert action.domains == ["github.com"]


def test_a_shell_action_has_no_method_even_when_the_request_carries_one():
    action = normalize(DecideRequest.model_validate(
        {"harness": "t", "tool": "shell", "raw": "curl -X DELETE https://github.com/o/r",
         "args": {"cwd": "/w", "method": "GET"}, "user_request": "x"}
    ))

    assert action.method is None
