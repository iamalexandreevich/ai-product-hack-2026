import json

import pytest
from pydantic import ValidationError

from agentgate.api.schemas import (
    METADATA_MAX_BYTES,
    RAW_MAX_BYTES,
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


def _field_names(exc: ValidationError) -> set[str]:
    """Names of the fields pydantic blamed for a ValidationError."""
    return {str(err["loc"][0]) for err in exc.errors()}


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


def _metadata_overhead_bytes() -> int:
    """Byte size of the JSON wrapper around {"k": <value>} with an empty value."""
    return len(json.dumps({"k": ""}, ensure_ascii=False).encode("utf-8"))


def test_metadata_accepted_at_byte_limit():
    overhead = _metadata_overhead_bytes()
    value = "v" * (METADATA_MAX_BYTES - overhead)
    payload = {"k": value}
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) == METADATA_MAX_BYTES
    r = _req(metadata=payload)
    assert r.metadata == payload


def test_metadata_rejected_over_byte_limit_ascii():
    overhead = _metadata_overhead_bytes()
    value = "v" * (METADATA_MAX_BYTES - overhead + 1)
    payload = {"k": value}
    assert len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) == METADATA_MAX_BYTES + 1
    with pytest.raises(ValidationError) as excinfo:
        _req(metadata=payload)
    assert "metadata" in _field_names(excinfo.value)


def test_metadata_rejected_over_byte_limit_multibyte():
    # Cyrillic is 2 bytes per char in UTF-8: pick a length that is comfortably
    # under METADATA_MAX_BYTES in *characters* but over it in *bytes*, so a
    # char-count check (bug) would wrongly accept this while a byte-count
    # check (spec) correctly rejects it.
    overhead = _metadata_overhead_bytes()
    n_chars = (METADATA_MAX_BYTES - overhead) // 2 + 10
    value = "ф" * n_chars
    payload = {"k": value}
    json_str = json.dumps(payload, ensure_ascii=False)
    assert len(json_str) < METADATA_MAX_BYTES
    assert len(json_str.encode("utf-8")) > METADATA_MAX_BYTES
    with pytest.raises(ValidationError) as excinfo:
        _req(metadata=payload)
    assert "metadata" in _field_names(excinfo.value)


def test_raw_accepted_at_byte_limit():
    value = "a" * RAW_MAX_BYTES
    r = _req(raw=value)
    assert len(r.raw.encode("utf-8")) == RAW_MAX_BYTES


def test_raw_rejected_over_byte_limit_ascii():
    value = "a" * (RAW_MAX_BYTES + 1)
    with pytest.raises(ValidationError) as excinfo:
        _req(raw=value)
    assert "raw" in _field_names(excinfo.value)


def test_raw_rejected_over_byte_limit_multibyte():
    # Same char-vs-byte trap as metadata above, but for the plain `raw` string
    # (no JSON wrapper): character count stays under RAW_MAX_BYTES while the
    # UTF-8 byte count exceeds it.
    n_chars = RAW_MAX_BYTES // 2 + 1
    value = "ф" * n_chars
    assert len(value) < RAW_MAX_BYTES
    assert len(value.encode("utf-8")) > RAW_MAX_BYTES
    with pytest.raises(ValidationError) as excinfo:
        _req(raw=value)
    assert "raw" in _field_names(excinfo.value)


def test_session_id_accepted_at_max_length():
    value = "s" * 128
    r = _req(session_id=value)
    assert r.session_id == value


def test_session_id_rejected_over_max_length():
    value = "s" * 129
    with pytest.raises(ValidationError) as excinfo:
        _req(session_id=value)
    assert "session_id" in _field_names(excinfo.value)


def test_harness_accepted_at_max_length():
    value = "h" * 64
    r = _req(harness=value)
    assert r.harness == value


def test_harness_rejected_over_max_length():
    value = "h" * 65
    with pytest.raises(ValidationError) as excinfo:
        _req(harness=value)
    assert "harness" in _field_names(excinfo.value)


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
