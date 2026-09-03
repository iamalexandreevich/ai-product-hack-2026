### Task 1: Каркас `service/` и настройки

**Files:**
- Create: `service/pyproject.toml`, `service/agentgate/__init__.py`, `service/agentgate/config.py`, `service/tests/__init__.py`, `service/tests/conftest.py`, `service/.gitignore`
- Test: `service/tests/test_config.py`

**Interfaces:**
- Produces: `agentgate.config.Settings` (pydantic-settings, префикс `AGENTGATE_`): `db_url: str`, `token: str | None`, `bind: str = "127.0.0.1:8400"`, `profiles_dir: Path = Path("profiles")`, `log_path: Path = Path("logs/decisions.jsonl")`, `default_profile: str = "default"`; свойство `bind_is_localhost: bool`; метод `validate_token_for_bind()` бросает `ValueError`, если `token` пуст и bind не localhost. `agentgate.config.get_settings() -> Settings`.

- [ ] **Step 1: pyproject и зависимости**

`service/pyproject.toml`:

```toml
[project]
name = "agentgate"
version = "0.1.0"
description = "AgentGate: harness-agnostic action gate for coding agents"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "sqlalchemy[asyncio]>=2.0.30",
  "asyncpg>=0.29",
  "alembic>=1.13",
  "bashlex>=0.18",
  "httpx>=0.27",
  "python-ulid>=2.7",
  "pyyaml>=6.0",
]

[dependency-groups]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "anyio>=4.4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["agentgate"]
```

`service/.gitignore`:

```
.venv/
logs/
__pycache__/
*.pyc
.pytest_cache/
```

`service/agentgate/__init__.py`: пустой. `service/tests/__init__.py`: пустой.

Run: `cd service && uv python pin 3.12 && uv sync`
Expected: создан `.venv`, `uv.lock`, без ошибок.

- [ ] **Step 2: Failing test для Settings**

`service/tests/test_config.py`:

```python
import pytest

from agentgate.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    s = Settings()
    assert s.bind == "127.0.0.1:8400"
    assert s.bind_is_localhost is True
    assert s.token is None
    assert s.default_profile == "default"


def test_non_localhost_bind_requires_token(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_BIND", "0.0.0.0:8400")
    s = Settings()
    assert s.bind_is_localhost is False
    with pytest.raises(ValueError):
        s.validate_token_for_bind()
    monkeypatch.setenv("AGENTGATE_TOKEN", "secret")
    Settings().validate_token_for_bind()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd service && uv run pytest tests/test_config.py -v`
Expected: FAIL, `ModuleNotFoundError: agentgate.config`.

- [ ] **Step 4: Реализация Settings**

`service/agentgate/config.py`:

```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCALHOST = {"127.0.0.1", "localhost", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTGATE_", extra="ignore")

    db_url: str
    token: str | None = None
    bind: str = "127.0.0.1:8400"
    profiles_dir: Path = Path("profiles")
    log_path: Path = Path("logs/decisions.jsonl")
    default_profile: str = "default"

    @property
    def bind_host(self) -> str:
        host, _, _ = self.bind.rpartition(":")
        return host.strip("[]") or self.bind

    @property
    def bind_port(self) -> int:
        _, _, port = self.bind.rpartition(":")
        return int(port)

    @property
    def bind_is_localhost(self) -> bool:
        return self.bind_host in _LOCALHOST

    def validate_token_for_bind(self) -> None:
        if not self.bind_is_localhost and not self.token:
            raise ValueError(
                "AGENTGATE_TOKEN is required when AGENTGATE_BIND is not localhost"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd service && uv run pytest tests/test_config.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add service/pyproject.toml service/uv.lock service/.python-version service/.gitignore service/agentgate service/tests
git commit -m "feat(service): project scaffold and settings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

