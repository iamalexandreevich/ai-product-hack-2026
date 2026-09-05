"""Benchmark case schema."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from schemas.case import (
    HISTORY_MAX_BYTES,
    HISTORY_MAX_TURNS,
    BenchmarkCase,
    DatasetSource,
    Difficulty,
    ServiceDecision,
    TurnAuthor,
    TurnRole,
)


def test_valid_case_parses(valid_case_dict):
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert case.id == "SAMPLE_001"
    assert case.difficulty is Difficulty.EASY
    assert case.expected_service_result is ServiceDecision.DENY
    assert case.is_benign is False


def test_acceptable_results_default_to_expected(valid_case_dict):
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert case.acceptable_service_results == [ServiceDecision.DENY]


def test_acceptable_results_must_contain_expected(valid_case_dict):
    valid_case_dict["acceptable_service_results"] = ["ask"]
    with pytest.raises(ValidationError, match="must be listed in acceptable"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_missing_human_req_is_rejected(valid_case_dict):
    del valid_case_dict["human_req"]
    with pytest.raises(ValidationError, match="human_req"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_empty_human_req_is_rejected(valid_case_dict):
    valid_case_dict["human_req"] = ""
    with pytest.raises(ValidationError, match="human_req"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_missing_assistant_tool_call_is_rejected(valid_case_dict):
    del valid_case_dict["assistant_tool_call"]
    with pytest.raises(ValidationError, match="assistant_tool_call"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_shell_requires_raw(valid_case_dict):
    valid_case_dict["assistant_tool_call"]["raw"] = "   "
    with pytest.raises(ValidationError, match="raw is required for tool=shell"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_mcp_call_requires_mcp_arguments(valid_case_dict):
    valid_case_dict["assistant_tool_call"] = {
        "tool": "mcp_call",
        "raw": "read_file(/home/dev/.aws/credentials)",
        "arguments": {"cwd": "/home/dev/repo"},
    }
    with pytest.raises(ValidationError, match="args.mcp is required"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_mcp_call_with_arguments_parses(valid_case_dict):
    valid_case_dict["assistant_tool_call"] = {
        "tool": "mcp_call",
        "raw": "read_file(/home/dev/.aws/credentials)",
        "arguments": {
            "cwd": "/home/dev/repo",
            "mcp": {
                "server": "unverified-fs",
                "tool": "read_file",
                "arguments": {"path": "/home/dev/.aws/credentials"},
            },
        },
    }
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert case.assistant_tool_call.arguments.mcp is not None
    assert case.assistant_tool_call.arguments.mcp.server == "unverified-fs"


def test_file_read_requires_paths(valid_case_dict):
    valid_case_dict["assistant_tool_call"] = {
        "tool": "file_read",
        "raw": "",
        "arguments": {"cwd": "/home/dev/repo"},
    }
    with pytest.raises(ValidationError, match="args.paths is required"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_network_requires_domains(valid_case_dict):
    valid_case_dict["assistant_tool_call"] = {
        "tool": "network",
        "raw": "fetch artifact",
        "arguments": {"cwd": "/home/dev/repo"},
    }
    with pytest.raises(ValidationError, match="args.domains is required"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_unknown_tool_is_rejected(valid_case_dict):
    valid_case_dict["assistant_tool_call"]["tool"] = "browser"
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(valid_case_dict)


def test_unknown_expected_result_is_rejected(valid_case_dict):
    valid_case_dict["expected_service_result"] = "block"
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(valid_case_dict)


def test_unknown_difficulty_is_rejected(valid_case_dict):
    valid_case_dict["difficulty"] = "impossible"
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(valid_case_dict)


def test_bad_id_pattern_is_rejected(valid_case_dict):
    valid_case_dict["id"] = "lowercase-id"
    with pytest.raises(ValidationError, match="must match"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_extra_fields_are_rejected(valid_case_dict):
    valid_case_dict["unexpected"] = "value"
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(valid_case_dict)


def test_attack_case_requires_payload(valid_case_dict):
    del valid_case_dict["attack_payload"]
    with pytest.raises(ValidationError, match="attack_payload is required"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_detection_true_conflicts_with_expected_allow(valid_case_dict):
    valid_case_dict["expected_service_result"] = "allow"
    with pytest.raises(ValidationError, match="inconsistent with expected allow"):
        BenchmarkCase.model_validate(valid_case_dict)


def test_benign_case_parses(benign_case_dict):
    case = BenchmarkCase.model_validate(benign_case_dict)
    assert case.is_benign is True
    assert case.expected_service_result is ServiceDecision.ALLOW


def test_benign_case_cannot_carry_payload(benign_case_dict):
    benign_case_dict["attack_payload"] = "something"
    with pytest.raises(ValidationError, match="must not carry attack_payload"):
        BenchmarkCase.model_validate(benign_case_dict)


def test_benign_case_cannot_expect_detection(benign_case_dict):
    benign_case_dict["expected_detection"] = True
    with pytest.raises(ValidationError, match="cannot expect detection"):
        BenchmarkCase.model_validate(benign_case_dict)


def test_none_location_cannot_be_combined(benign_case_dict):
    benign_case_dict["attack_location"] = ["none", "human_req"]
    with pytest.raises(ValidationError, match="cannot be combined"):
        BenchmarkCase.model_validate(benign_case_dict)


# -- dataset source ----------------------------------------------------------


def test_dataset_source_defaults_to_team(valid_case_dict):
    """Every case authored in this repository belongs to the team population."""
    case = BenchmarkCase.model_validate(valid_case_dict)
    assert case.dataset_source is DatasetSource.TEAM


def test_dataset_source_can_declare_an_imported_baseline(valid_case_dict):
    case = BenchmarkCase.model_validate(valid_case_dict | {"dataset_source": "baseline"})
    assert case.dataset_source is DatasetSource.BASELINE


def test_unknown_dataset_source_is_rejected(valid_case_dict):
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(valid_case_dict | {"dataset_source": "borrowed"})


# -- dialogue history --------------------------------------------------------


def test_a_case_without_history_has_an_empty_one(valid_case_dict):
    """Every case written at the v1 boundary stays valid and sends no dialogue."""
    assert BenchmarkCase.model_validate(valid_case_dict).history == []


def test_history_turn_parses_every_contract_field(history_case_dict):
    case = BenchmarkCase.model_validate(history_case_dict)
    assert [t.role for t in case.history][:2] == [TurnRole.HUMAN, TurnRole.HUMAN]
    toolresult = case.history[-1]
    assert toolresult.role is TurnRole.TOOLRESULT
    assert toolresult.author is TurnAuthor.SYSTEM
    assert (toolresult.tool, toolresult.call_id) == ("bash", "c1")


def test_a_turn_may_forge_its_author(history_case_dict):
    """role=human with author=agent is legal on the wire, and is an attack to measure."""
    history_case_dict["history"][1]["author"] = "agent"
    case = BenchmarkCase.model_validate(history_case_dict)
    assert case.history[1].role is TurnRole.HUMAN
    assert case.history[1].author is TurnAuthor.AGENT


def test_unknown_role_or_author_is_rejected(history_case_dict):
    for field, value in (("role", "operator"), ("author", "root")):
        payload = json.loads(json.dumps(history_case_dict))
        payload["history"][0][field] = value
        with pytest.raises(ValidationError):
            BenchmarkCase.model_validate(payload)


def test_extra_keys_on_a_turn_are_rejected(history_case_dict):
    history_case_dict["history"][0]["trusted"] = True
    with pytest.raises(ValidationError):
        BenchmarkCase.model_validate(history_case_dict)


def test_attack_location_history_requires_a_dialogue(history_case_dict):
    history_case_dict["history"] = []
    with pytest.raises(ValidationError, match="requires a non-empty history"):
        BenchmarkCase.model_validate(history_case_dict)


def test_a_benign_case_may_carry_history(benign_case_dict, history_case_dict):
    """The friction control: a long legitimate dialogue in front of a legitimate action."""
    benign_case_dict["history"] = history_case_dict["history"]
    case = BenchmarkCase.model_validate(benign_case_dict)
    assert case.is_benign
    assert len(case.history) == 5


def test_history_longer_than_the_wire_limit_is_rejected(history_case_dict):
    """A case the service refuses at stage 0 measures nothing, so it may not exist."""
    turn = {"role": "human", "author": "human", "content": "x"}
    history_case_dict["history"] = [turn] * (HISTORY_MAX_TURNS + 1)
    with pytest.raises(ValidationError, match="200 turns"):
        BenchmarkCase.model_validate(history_case_dict)


def test_tool_and_call_id_count_against_the_byte_budget(history_case_dict):
    """Exactly how the service counts: content plus tool plus call_id, not content alone."""
    filling = "x" * (HISTORY_MAX_BYTES - 4)
    turn = {
        "role": "toolresult",
        "author": "system",
        "content": filling,
        "tool": "abcd",
        "call_id": "e",
    }
    history_case_dict["history"] = [turn]
    with pytest.raises(ValidationError, match="bytes"):
        BenchmarkCase.model_validate(history_case_dict)

    turn.pop("tool")
    turn.pop("call_id")
    assert len(BenchmarkCase.model_validate(history_case_dict).history) == 1


# -- difficulty --------------------------------------------------------------


def test_ultra_hard_is_a_difficulty_a_case_may_declare(history_case_dict):
    case = BenchmarkCase.model_validate(history_case_dict | {"difficulty": "ultra_hard"})
    assert case.difficulty is Difficulty.ULTRA_HARD
