import json

import pytest
from pydantic import ValidationError

from agentgate.api.schemas import (
    METADATA_MAX_BYTES,
    USER_REQUEST_MAX_CHARS,
    DecideRequest,
    DecideResponse,
    DecisionKind,
    LatencyMs,
    Tool,
)


def _req(**over):
    base = dict(
        harness="opencode",
        tool="shell",
        raw="ls -la",
        args={"cwd": "/home/u/repo"},
        user_request="покажи файлы",
    )
    base.update(over)
    return DecideRequest.model_validate(base)


def test_minimal_request_ok():
    r = _req()
    assert r.tool is Tool.shell
    assert r.session_id is None
    assert r.profile_id is None
    assert r.metadata == {}


def test_shell_requires_raw():
    with pytest.raises(ValidationError):
        _req(raw="")


def test_file_write_without_raw_ok():
    r = _req(tool="file_write", raw="", args={"cwd": "/r", "paths": ["/r/a.py"]})
    assert r.args.paths == ["/r/a.py"]


def test_user_request_truncated_keeps_tail():
    text = "x" * 3000 + "TAIL"
    r = _req(user_request=text)
    assert len(r.user_request) == USER_REQUEST_MAX_CHARS
    assert r.user_request.endswith("TAIL")


def test_metadata_size_limit():
    big = {"k": "v" * (METADATA_MAX_BYTES + 1)}
    with pytest.raises(ValidationError):
        _req(metadata=big)


def test_unknown_tool_rejected():
    with pytest.raises(ValidationError):
        _req(tool="browser")


def test_response_roundtrip():
    resp = DecideResponse(
        decision=DecisionKind.deny,
        reason="r",
        suggest="s",
        stage=1,
        rule_id="hard-deny.exfil",
        latency_ms=LatencyMs(stage1=1, stage2=None, total=1),
        decision_id="01J0000000000000000000000",
    )
    data = json.loads(resp.model_dump_json())
    assert data["decision"] == "deny"
    assert data["model"] is None
    assert data["cached"] is False
