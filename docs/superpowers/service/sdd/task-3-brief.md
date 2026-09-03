### Task 3: Профили политики

**Files:**
- Create: `service/agentgate/profiles/__init__.py`, `service/agentgate/profiles/schema.py`, `service/agentgate/profiles/loader.py`, `service/profiles/default-dev.yaml`
- Test: `service/tests/test_profiles.py`

**Interfaces:**
- Produces (`agentgate.profiles.schema`):
  - `class NetworkMode(str, Enum)`: `off`, `allowlist`, `ask`, `open`.
  - `class Network(BaseModel)`: `mode: NetworkMode = allowlist`, `allowed_domains: list[str] = []`.
  - `class ModelConfig(BaseModel)`: `base_url: str`, `model: str`, `api_key_env: str | None = None`, `timeout_ms: int = 3000`, `structured_output: bool = True`.
  - `class ModelsConfig(BaseModel)`: `default: str`, `configs: dict[str, ModelConfig]`; валидатор: `default in configs`.
  - `class DenyWindow(BaseModel)`: `count: int = 10`, `of_last: int = 50`.
  - `class Escalation(BaseModel)`: `deny_consecutive: int = 3`, `deny_window: DenyWindow`.
  - `class Prose(BaseModel)`: `environment: str = ""`, `allow: str = ""`, `soft_deny: str = ""`.
  - `class Profile(BaseModel)`: `id: str`, `allowed_paths: list[str]`, `protected_paths: list[str]`, `protected_branches: list[str] = ["main","master"]`, `network: Network`, `safe_prefixes: list[list[str]] = []`, `models: ModelsConfig`, `escalation: Escalation`, `prose: Prose`, `rules: list[dict] = []`, `workspace: str | None = None`. Методы: `profile_hash() -> str` (sha256 от `model_dump_json(exclude={"workspace"})` с сортировкой ключей), `resolved_allowed_paths() -> list[str]` (подстановка `${WORKSPACE}` и `~`), `model_config_for(name: str | None) -> tuple[str, ModelConfig]` (`KeyError` на неизвестное имя), `public_dict() -> dict` (без значений секретов, там их и нет: только имена переменных).
- Produces (`agentgate.profiles.loader`): `load_profiles(dir: Path) -> dict[str, Profile]` (все `*.yaml`, ключ — `id`; дубликат `id` → `ValueError`; ошибка валидации → `ValueError` с именем файла); `detect_workspace(cwd: str) -> str` (ближайший родитель с `.git`, иначе `cwd`); `with_workspace(profile: Profile, cwd: str) -> Profile` (копия с `workspace` заполненным).

- [ ] **Step 1: Failing tests**

`service/tests/test_profiles.py`:

```python
from pathlib import Path

import pytest
import yaml

from agentgate.profiles.loader import detect_workspace, load_profiles, with_workspace
from agentgate.profiles.schema import NetworkMode, Profile

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


def test_hash_ignores_workspace_and_is_stable():
    a = Profile.model_validate(MINIMAL)
    b = with_workspace(a, "/tmp/w")
    assert a.profile_hash() == b.profile_hash()
    assert len(a.profile_hash()) == 64


def test_resolved_allowed_paths(tmp_path):
    p = with_workspace(Profile.model_validate(MINIMAL), str(tmp_path))
    assert p.resolved_allowed_paths() == [str(tmp_path)]


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
    with pytest.raises(ValueError):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd service && uv run pytest tests/test_profiles.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.profiles`.

- [ ] **Step 3: Реализация schema.py**

`service/agentgate/profiles/__init__.py`: пустой.

`service/agentgate/profiles/schema.py`:

```python
import hashlib
import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class NetworkMode(str, Enum):
    off = "off"
    allowlist = "allowlist"
    ask = "ask"
    open = "open"


class Network(BaseModel):
    mode: NetworkMode = NetworkMode.allowlist
    allowed_domains: list[str] = Field(default_factory=list)


class ModelConfig(BaseModel):
    base_url: str
    model: str
    api_key_env: str | None = None
    timeout_ms: int = 3000
    structured_output: bool = True


class ModelsConfig(BaseModel):
    default: str
    configs: dict[str, ModelConfig]

    @model_validator(mode="after")
    def _default_exists(self) -> "ModelsConfig":
        if self.default not in self.configs:
            raise ValueError(f"models.default '{self.default}' is not in models.configs")
        return self

    def model_config_for(self, name: str | None) -> tuple[str, ModelConfig]:
        key = name or self.default
        return key, self.configs[key]


class DenyWindow(BaseModel):
    count: int = 10
    of_last: int = 50


class Escalation(BaseModel):
    deny_consecutive: int = 3
    deny_window: DenyWindow = Field(default_factory=DenyWindow)


class Prose(BaseModel):
    environment: str = ""
    allow: str = ""
    soft_deny: str = ""


class Profile(BaseModel):
    id: str
    allowed_paths: list[str]
    protected_paths: list[str]
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])
    network: Network = Field(default_factory=Network)
    safe_prefixes: list[list[str]] = Field(default_factory=list)
    models: ModelsConfig
    escalation: Escalation = Field(default_factory=Escalation)
    prose: Prose = Field(default_factory=Prose)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    workspace: str | None = None

    def profile_hash(self) -> str:
        payload = self.model_dump_json(exclude={"workspace"})
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _expand(self, p: str) -> str:
        ws = self.workspace or ""
        return os.path.expanduser(p.replace("${WORKSPACE}", ws))

    def resolved_allowed_paths(self) -> list[str]:
        return [os.path.normpath(self._expand(p)) for p in self.allowed_paths]

    def resolved_protected_paths(self) -> list[str]:
        return [self._expand(p) for p in self.protected_paths]

    def public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
```

- [ ] **Step 4: Реализация loader.py и default-dev.yaml**

`service/agentgate/profiles/loader.py`:

```python
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
```

`service/profiles/default-dev.yaml`:

```yaml
id: default
allowed_paths: ["${WORKSPACE}", "/tmp/agentgate-scratch"]
protected_paths:
  - ".env*"
  - ".git/hooks/**"
  - ".opencode/**"
  - ".kilo/**"
  - ".claude/**"
  - ".codex/**"
  - "AGENTS.md"
  - "SKILL.md"
  - ".cursorrules"
  - "~/.ssh/**"
  - "~/.aws/**"
  - "~/.kube/**"
protected_branches: ["main", "master", "release/*"]
network:
  mode: allowlist
  allowed_domains: ["registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "github.com", "api.github.com"]
safe_prefixes:
  - ["npm", "test"]
  - ["npm", "run", "lint"]
  - ["pytest"]
  - ["cargo", "test"]
models:
  default: sonnet
  configs:
    sonnet:
      base_url: "https://openrouter.ai/api/v1"
      model: "anthropic/claude-sonnet-4-6"
      api_key_env: OPENROUTER_API_KEY
      timeout_ms: 3000
      structured_output: true
    local:
      base_url: "http://localhost:8000/v1"
      model: "qwen3.5-4b"
      api_key_env: null
      timeout_ms: 1500
      structured_output: true
escalation:
  deny_consecutive: 3
  deny_window: {count: 10, of_last: 50}
prose:
  environment: ""
  allow: ""
  soft_deny: ""
rules: []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd service && uv run pytest tests/test_profiles.py -v`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add service/agentgate/profiles service/profiles service/tests/test_profiles.py
git commit -m "feat(service): policy profiles schema and loader

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

