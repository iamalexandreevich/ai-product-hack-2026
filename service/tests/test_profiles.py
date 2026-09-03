import os
from pathlib import Path

import pytest
import yaml

from agentgate.profiles.loader import detect_workspace, interpolate_env, load_profiles, with_workspace
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


# --- env-var interpolation in profile YAML loading ---


def test_interpolate_env_default_used_when_unset(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_NAME", raising=False)
    assert interpolate_env("${OPENROUTER_MODEL_NAME:-google/gemini-3.8-flash}") == "google/gemini-3.8-flash"


def test_interpolate_env_value_used_when_set(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash")
    assert interpolate_env("${OPENROUTER_MODEL_NAME:-google/gemini-3.8-flash}") == "google/gemini-2.0-flash"


def test_interpolate_env_bare_var_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_VAR_XYZ", raising=False)
    assert interpolate_env("${SOME_UNSET_VAR_XYZ}") == ""


def test_interpolate_env_bare_var_used_when_set(monkeypatch):
    monkeypatch.setenv("SOME_SET_VAR_XYZ", "hello")
    assert interpolate_env("${SOME_SET_VAR_XYZ}") == "hello"


def test_interpolate_env_literal_string_unchanged():
    assert interpolate_env("anthropic/claude-sonnet-4-6") == "anthropic/claude-sonnet-4-6"
    assert interpolate_env("no dollar signs here at all") == "no dollar signs here at all"


def test_interpolate_env_workspace_placeholder_left_untouched(monkeypatch):
    # ${WORKSPACE} is resolved later by Profile._expand from the runtime
    # workspace, not from os.environ — env interpolation must not consume it,
    # even if a real WORKSPACE env var happens to be set.
    monkeypatch.setenv("WORKSPACE", "/should/not/be/used")
    assert interpolate_env("${WORKSPACE}") == "${WORKSPACE}"
    assert interpolate_env("${WORKSPACE}/src") == "${WORKSPACE}/src"


def test_interpolate_env_recurses_through_dicts_and_lists(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash")
    data = {
        "id": "t",
        "allowed_paths": ["${WORKSPACE}"],
        "models": {"default": "m", "configs": {"m": {"model": "${OPENROUTER_MODEL_NAME:-fallback}"}}},
        "list_of_ints": [1, 2, 3],
    }
    out = interpolate_env(data)
    assert out["allowed_paths"] == ["${WORKSPACE}"]
    assert out["models"]["configs"]["m"]["model"] == "google/gemini-2.0-flash"
    assert out["list_of_ints"] == [1, 2, 3]


def test_load_profiles_applies_env_interpolation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash")
    data = dict(
        MINIMAL,
        models={"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "${OPENROUTER_MODEL_NAME:-fallback}"}}},
    )
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(data))
    profiles = load_profiles(tmp_path)
    assert profiles["t"].models.configs["m"].model == "google/gemini-2.0-flash"


def test_load_profiles_applies_env_interpolation_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_NAME", raising=False)
    data = dict(
        MINIMAL,
        models={"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "${OPENROUTER_MODEL_NAME:-fallback}"}}},
    )
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(data))
    profiles = load_profiles(tmp_path)
    assert profiles["t"].models.configs["m"].model == "fallback"


def test_shipped_default_profile_default_model_is_gemini(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_NAME", raising=False)
    shipped = Path(__file__).resolve().parents[1] / "profiles"
    profiles = load_profiles(shipped)
    assert profiles["default"].models.default == "gemini"
    key, cfg = profiles["default"].models.model_config_for(None)
    assert key == "gemini"
    assert cfg.model == "google/gemini-3.8-flash"
    assert cfg.base_url == "https://openrouter.ai/api/v1"
    assert cfg.api_key_env == "OPENROUTER_API_KEY"


def test_shipped_default_profile_model_override_via_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash")
    shipped = Path(__file__).resolve().parents[1] / "profiles"
    profiles = load_profiles(shipped)
    _, cfg = profiles["default"].models.model_config_for(None)
    assert cfg.model == "google/gemini-2.0-flash"
