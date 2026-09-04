"""Benchmark case schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.case import BenchmarkCase, Difficulty, ServiceDecision


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
