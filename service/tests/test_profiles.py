import os
from pathlib import Path

import pytest
import yaml

from agentgate.profiles.loader import detect_workspace, load_profiles, with_workspace
from agentgate.profiles.schema import DenyWindow, NetworkMode, Profile

MINIMAL = {
    "id": "t",
    "allowed_paths": ["${WORKSPACE}"],
    "protected_paths": [".env*"],
    "network": {"mode": "allowlist", "allowed_domains": ["pypi.org"]},
    "models": {"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "qwen"}}},
}


def test_minimal_profile_defaults():
    p = Profile.model_validate(MINIMAL)
    assert p.escalation.deny_consecutive == 3
    assert p.escalation.deny_window.of_last == 50
    assert p.network.mode is NetworkMode.allowlist
    assert p.models.model_config_for(None)[0] == "m"
    assert p.models.model_config_for("m")[1].timeout_ms == 3000
    with pytest.raises(KeyError):
        p.models.model_config_for("nope")


def test_default_model_must_exist():
    bad = dict(MINIMAL, models={"default": "zzz", "configs": MINIMAL["models"]["configs"]})
    with pytest.raises(ValueError):
        Profile.model_validate(bad)


def test_deny_window_of_last_zero_rejected():
    # of_last=0 makes list[-0:] the WHOLE list in Python, silently defeating
    # an operator's attempt to disable the window check.
    with pytest.raises(ValueError):
        DenyWindow(of_last=0)


def test_deny_window_count_zero_rejected():
    # count=0 makes window.count("deny") >= 0 trivially true: escalates on everything.
    with pytest.raises(ValueError):
        DenyWindow(count=0)


def test_deny_window_count_greater_than_of_last_rejected():
    # count > of_last: the window can never hold `count` denials, so escalation
    # never fires. Fails open, silently, which is worse than the of_last=0 bug.
    with pytest.raises(ValueError):
        DenyWindow(count=5, of_last=3)


def test_load_profiles_rejects_degenerate_deny_window(tmp_path):
    bad = dict(MINIMAL, escalation={"deny_window": {"count": 10, "of_last": 0}})
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="bad.yaml"):
        load_profiles(tmp_path)


def test_hash_ignores_workspace_and_is_stable():
    a = Profile.model_validate(MINIMAL)
    b = with_workspace(a, "/tmp/w")
    assert a.profile_hash() == b.profile_hash()
    assert len(a.profile_hash()) == 64


def test_resolved_allowed_paths(tmp_path):
    p = with_workspace(Profile.model_validate(MINIMAL), str(tmp_path))
    assert p.resolved_allowed_paths() == [str(tmp_path)]


def test_resolved_allowed_paths_workspace_and_tilde(tmp_path):
    ws = str(tmp_path)
    p = Profile.model_validate(
        dict(MINIMAL, allowed_paths=["${WORKSPACE}/src", "~/.cache/agentgate"])
    ).model_copy(update={"workspace": ws})
    assert p.resolved_allowed_paths() == [
        os.path.normpath(f"{ws}/src"),
        os.path.normpath(os.path.expanduser("~/.cache/agentgate")),
    ]


def test_resolved_protected_paths_workspace_and_tilde(tmp_path):
    ws = str(tmp_path)
    p = Profile.model_validate(
        dict(MINIMAL, protected_paths=["${WORKSPACE}/.env*", "~/.ssh/**"])
    ).model_copy(update={"workspace": ws})
    assert p.resolved_protected_paths() == [
        f"{ws}/.env*",
        os.path.expanduser("~/.ssh/**"),
    ]


def test_detect_workspace(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "src" / "pkg").mkdir(parents=True)
    assert detect_workspace(str(tmp_path / "repo" / "src" / "pkg")) == str(tmp_path / "repo")
    assert detect_workspace(str(tmp_path)) == str(tmp_path)


def test_load_profiles_dir(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(MINIMAL))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(dict(MINIMAL, id="u")))
    profiles = load_profiles(tmp_path)
    assert set(profiles) == {"t", "u"}


def test_load_profiles_duplicate_id(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(MINIMAL))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(MINIMAL))
    with pytest.raises(ValueError, match="b.yaml"):
        load_profiles(tmp_path)


def test_load_profiles_invalid_raises_with_filename(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: x\n")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_profiles(tmp_path)


def test_shipped_default_profile_loads():
    shipped = Path(__file__).resolve().parents[1] / "profiles"
    profiles = load_profiles(shipped)
    assert "default" in profiles
    assert profiles["default"].models.default in profiles["default"].models.configs
