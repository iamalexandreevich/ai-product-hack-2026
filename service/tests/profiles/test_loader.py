from pathlib import Path

import pytest
import yaml

from agentgate.profiles.loader import detect_workspace, interpolate_env, load_profiles
from tests.factories import minimal_profile_data


def test_load_profiles_rejects_degenerate_deny_window(tmp_path):
    bad = minimal_profile_data(escalation={"deny_window": {"count": 10, "of_last": 0}})
    (tmp_path / "bad.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError, match="bad.yaml"):
        load_profiles(tmp_path)


def test_detect_workspace(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "src" / "pkg").mkdir(parents=True)
    assert detect_workspace(str(tmp_path / "repo" / "src" / "pkg")) == str(tmp_path / "repo")
    assert detect_workspace(str(tmp_path)) == str(tmp_path)


def test_load_profiles_dir(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(minimal_profile_data()))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(minimal_profile_data(id="u")))
    profiles = load_profiles(tmp_path)
    assert set(profiles) == {"t", "u"}


def test_load_profiles_duplicate_id(tmp_path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(minimal_profile_data()))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(minimal_profile_data()))
    with pytest.raises(ValueError, match="b.yaml"):
        load_profiles(tmp_path)


def test_load_profiles_invalid_raises_with_filename(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: x\n")
    with pytest.raises(ValueError, match="bad.yaml"):
        load_profiles(tmp_path)


def test_shipped_default_profile_loads():
    shipped = Path(__file__).resolve().parents[2] / "profiles"
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
    # ${WORKSPACE} is resolved later, when the profile is bound to a session's
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
    data = minimal_profile_data(
        models={"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "${OPENROUTER_MODEL_NAME:-fallback}"}}},
    )
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(data))
    profiles = load_profiles(tmp_path)
    assert profiles["t"].models.configs["m"].model == "google/gemini-2.0-flash"


def test_load_profiles_applies_env_interpolation_default(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_NAME", raising=False)
    data = minimal_profile_data(
        models={"default": "m", "configs": {"m": {"base_url": "http://x/v1", "model": "${OPENROUTER_MODEL_NAME:-fallback}"}}},
    )
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(data))
    profiles = load_profiles(tmp_path)
    assert profiles["t"].models.configs["m"].model == "fallback"


def test_shipped_default_profile_default_model_is_gpt_4_1_mini(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_NAME", raising=False)
    shipped = Path(__file__).resolve().parents[2] / "profiles"
    profiles = load_profiles(shipped)
    assert profiles["default"].models.default == "primary"
    key, cfg = profiles["default"].models.model_config_for(None)
    assert key == "primary"
    assert cfg.model == "openai/gpt-4.1-mini"
    assert cfg.base_url == "https://openrouter.ai/api/v1"
    assert cfg.api_key_env == "OPENROUTER_API_KEY"
    # stage 2 is not on the 1 ms budget; a hosted model needs real headroom
    assert cfg.timeout_ms >= 8000


def test_shipped_default_profile_model_override_via_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash")
    shipped = Path(__file__).resolve().parents[2] / "profiles"
    profiles = load_profiles(shipped)
    _, cfg = profiles["default"].models.model_config_for(None)
    assert cfg.model == "google/gemini-2.0-flash"


def test_interpolate_env_default_used_when_empty(monkeypatch):
    # shell `:-` semantics: an empty value (compose passes ${VAR:-} as "")
    # must fall back to the default, not blank the field.
    monkeypatch.setenv("OPENROUTER_MODEL_NAME", "")
    assert interpolate_env("${OPENROUTER_MODEL_NAME:-openai/gpt-4.1-mini}") == "openai/gpt-4.1-mini"
