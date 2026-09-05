import json

import pytest
from pydantic import ValidationError

from agentgate.api.schemas import (
    HISTORY_MAX_BYTES,
    HISTORY_MAX_TURNS,
    METADATA_MAX_BYTES,
    PROTOCOL,
    RAW_MAX_BYTES,
    RULE_PATTERN_MAX_CHARS,
    RULES_MAX_BYTES,
    RULES_MAX_PATTERNS,
    USER_REQUEST_MAX_CHARS,
    Author,
    DecideRequest,
    DecideResponse,
    DecisionKind,
    LatencyMs,
    Tool,
    Turn,
    TurnRole,
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


# --- v2: history, protocol -------------------------------------------------


def _turn(**over) -> dict:
    base = dict(role="human", author="human", content="fix the build")
    base.update(over)
    return base


def test_history_defaults_to_empty_and_protocol_to_current():
    r = _req()
    assert r.history == [] and r.protocol == PROTOCOL == 1


def test_turn_parses_role_author_tool_and_call_id():
    r = _req(history=[_turn(role="toolresult", author="system", tool="bash", call_id="c1", content="ok")])
    turn = r.history[0]
    assert turn.role is TurnRole.toolresult and turn.author is Author.system
    assert turn.tool == "bash" and turn.call_id == "c1" and turn.content == "ok"


def test_turn_is_immutable():
    turn = Turn(role="human", author="human", content="x")
    with pytest.raises(ValidationError):
        turn.content = "y"


@pytest.mark.parametrize("field", ["role", "author"], ids=["role", "author"])
def test_unknown_role_or_author_is_rejected(field):
    with pytest.raises(ValidationError) as exc:
        _req(history=[_turn(**{field: "wizard"})])
    assert "history" in _field_names(exc.value)


def test_history_over_turn_limit_is_rejected():
    with pytest.raises(ValidationError, match=f"exceeds {HISTORY_MAX_TURNS} turns"):
        _req(history=[_turn()] * (HISTORY_MAX_TURNS + 1))


def test_history_at_turn_limit_is_accepted():
    assert len(_req(history=[_turn()] * HISTORY_MAX_TURNS).history) == HISTORY_MAX_TURNS


def test_history_over_byte_limit_is_rejected():
    # Cyrillic is two bytes per character: half the byte limit in characters,
    # plus one more character, is one byte over the limit.
    big = _turn(content="ж" * (HISTORY_MAX_BYTES // 2 + 1))
    with pytest.raises(ValidationError, match=f"exceeds {HISTORY_MAX_BYTES} bytes"):
        _req(history=[big])


def test_history_at_byte_limit_is_accepted():
    exact = _turn(content="ж" * (HISTORY_MAX_BYTES // 2))
    assert len(_req(history=[exact]).history) == 1


def test_tool_and_call_id_count_toward_the_byte_limit():
    almost = _turn(content="x" * (HISTORY_MAX_BYTES - 4), tool="abcd")
    assert len(_req(history=[almost]).history) == 1
    with pytest.raises(ValidationError, match="exceeds"):
        _req(history=[_turn(content="x" * (HISTORY_MAX_BYTES - 4), tool="abcd", call_id="e")])


def test_turn_tool_and_call_id_lengths_are_capped():
    with pytest.raises(ValidationError):
        _req(history=[_turn(tool="t" * 65)])
    with pytest.raises(ValidationError):
        _req(history=[_turn(call_id="c" * 129)])


def test_history_with_a_lone_surrogate_is_measured_not_crashed():
    assert len(_req(history=[_turn(content="\ud800")]).history) == 1


def test_unsupported_protocol_is_rejected():
    with pytest.raises(ValidationError, match="unsupported protocol 2"):
        _req(protocol=2)


def test_response_carries_protocol_by_default():
    r = DecideResponse(decision=DecisionKind.allow, stage=1, latency_ms=LatencyMs(total=1), decision_id="01J")
    assert r.protocol == PROTOCOL


def test_identity_digest_ignores_metadata():
    assert _req(metadata={"run": "a"}).identity_digest() == _req(metadata={"run": "b"}).identity_digest()


def test_identity_digest_separates_requests_differing_only_in_paths():
    one = _req(tool="file_write", raw="", args={"cwd": "/r", "paths": ["/r/ok.txt"]})
    other = _req(tool="file_write", raw="", args={"cwd": "/r", "paths": ["/r/.env"]})
    assert one.identity_digest() != other.identity_digest()


def _rules(**over) -> dict:
    base = dict(version=1, level="medium", allow=["git status", "git diff*"], ask=["curl *"], deny=["sudo *", "**/.env"])
    base.update(over)
    return base


def test_rules_default_to_none_and_call_id_to_none():
    r = _req()
    assert r.rules is None and r.call_id is None


def test_rules_parse_and_are_immutable():
    r = _req(rules=_rules())
    assert r.rules.level == "medium" and r.rules.deny == ["sudo *", "**/.env"]
    with pytest.raises(ValidationError):
        r.rules.level = "high"


def test_rules_level_defaults_to_custom():
    assert _req(rules={k: v for k, v in _rules().items() if k != "level"}).rules.level == "custom"


def test_unknown_rules_version_is_rejected():
    with pytest.raises(ValidationError, match="unsupported rules version 2"):
        _req(rules=_rules(version=2))


def test_rules_over_pattern_count_are_rejected():
    with pytest.raises(ValidationError, match=f"exceed {RULES_MAX_PATTERNS} patterns"):
        _req(rules=_rules(allow=["a"] * (RULES_MAX_PATTERNS + 1), ask=[], deny=[]))


def test_rules_pattern_over_length_is_rejected():
    with pytest.raises(ValidationError, match=f"exceeds {RULE_PATTERN_MAX_CHARS} chars"):
        _req(rules=_rules(deny=["x" * (RULE_PATTERN_MAX_CHARS + 1)]))


def test_rules_over_byte_limit_are_rejected():
    many = ["ж" * 100] * 90  # 9000 chars, 18000 bytes
    with pytest.raises(ValidationError, match=f"exceed {RULES_MAX_BYTES} bytes"):
        _req(rules=_rules(allow=many, ask=[], deny=[]))


def test_call_id_is_capped():
    assert _req(call_id="c" * 128).call_id == "c" * 128
    with pytest.raises(ValidationError):
        _req(call_id="c" * 129)
