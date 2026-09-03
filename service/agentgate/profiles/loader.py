import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from agentgate.profiles.schema import Profile


def load_profiles(directory: Path) -> dict[str, Profile]:
    profiles: dict[str, Profile] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
