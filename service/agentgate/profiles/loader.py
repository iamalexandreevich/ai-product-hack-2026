import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agentgate.profiles.schema import Profile

# ${NAME} or ${NAME:-default} — the only two forms supported. No other shell
# expansion syntax (${NAME:=x}, ${NAME:+x}, command substitution, etc.) is
# recognized; a pattern that doesn't match this regex is left as-is.
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-(?P<default>[^}]*))?\}")

# WORKSPACE is not an OS environment variable: it is filled in later, per
# request, by Profile._expand from the detected workspace directory. Env
# interpolation must leave "${WORKSPACE}" untouched so that later step can
# still find it.
_RESERVED_PLACEHOLDERS = {"WORKSPACE"}


def _interpolate_string(text: str) -> str:
    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name in _RESERVED_PLACEHOLDERS:
            return match.group(0)
        default = match.group("default")
        value = os.environ.get(name)
        if default is not None:
            # ${VAR:-default}: shell `:-` semantics — the default applies when
            # VAR is unset OR empty. Compose passes `${VAR:-}` through as an
            # empty string, so treating empty as "use default" is what keeps
            # an unset OPENROUTER_MODEL_NAME from blanking the model.
            return value if value else default
        # ${VAR}: the value if set (even empty), else empty.
        return value if value is not None else ""

    return _ENV_VAR_PATTERN.sub(_replace, text)


def interpolate_env(value: Any) -> Any:
    """Recursively substitute ``${VAR}`` / ``${VAR:-default}`` in string leaves.

    ``${VAR}`` resolves to ``os.environ[VAR]`` if set, else empty string.
    ``${VAR:-default}`` resolves to the env value if set, else ``default``.
    No other shell-expansion syntax is supported. The literal placeholder
    ``${WORKSPACE}`` is always left untouched (see ``_RESERVED_PLACEHOLDERS``).
    Non-string values (int, bool, None, ...) pass through unchanged.
    """
    if isinstance(value, str):
        return _interpolate_string(value)
    if isinstance(value, dict):
        return {k: interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_env(v) for v in value]
    return value


def load_profiles(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            data = interpolate_env(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
            profile = Profile.model_validate(data)
        except (ValidationError, yaml.YAMLError) as exc:
            raise ValueError(f"invalid profile {path.name}: {exc}") from exc
        if profile.id in profiles:
            raise ValueError(f"duplicate profile id '{profile.id}' in {path.name}")
        profiles[profile.id] = profile
    return profiles


def detect_workspace(cwd: str) -> str:
    current = os.path.abspath(cwd)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(cwd)
        current = parent


def with_workspace(profile: Profile, cwd: str) -> Profile:
    return profile.model_copy(update={"workspace": detect_workspace(cwd)})
