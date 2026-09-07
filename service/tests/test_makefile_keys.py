"""`make key|keys|key-revoke` build the right remote command (checked via `make -n`)."""

import subprocess
from pathlib import Path

import pytest

MAKEFILE_DIR = Path(__file__).resolve().parents[1]


def _make(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["make", "-n", "-C", str(MAKEFILE_DIR), *args],
        capture_output=True,
        text=True,
    )


def test_key_runs_create_inside_gate_container_over_ssh():
    result = _make("key", "LABEL=kilo-ci")
    assert result.returncode == 0, result.stderr
    assert "ssh agentgate" in result.stdout
    assert "cd /opt/agentgate" in result.stdout
    assert "exec -T gate uv run python -m agentgate keys create --label 'kilo-ci'" in result.stdout
    assert "--expires" not in result.stdout


def test_key_passes_expires_when_given():
    result = _make("key", "LABEL=kilo-ci", "EXPIRES=90d")
    assert result.returncode == 0, result.stderr
    assert "--label 'kilo-ci' --expires 90d" in result.stdout


def test_key_without_label_fails_before_touching_the_server():
    result = _make("key")
    assert result.returncode != 0
    assert "LABEL is required" in result.stdout + result.stderr
    assert "ssh" not in result.stdout


def test_keys_lists_over_ssh():
    result = _make("keys")
    assert result.returncode == 0, result.stderr
    assert "exec -T gate uv run python -m agentgate keys list" in result.stdout


def test_key_revoke_passes_id():
    result = _make("key-revoke", "ID=01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert result.returncode == 0, result.stderr
    assert "keys revoke 01ARZ3NDEKTSV4RRFFQ69G5FAV" in result.stdout


def test_key_revoke_without_id_fails():
    result = _make("key-revoke")
    assert result.returncode != 0
    assert "ID is required" in result.stdout + result.stderr
