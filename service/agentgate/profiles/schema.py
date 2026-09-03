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
    count: int = Field(default=10, ge=1)
    of_last: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def _count_within_window(self) -> "DenyWindow":
        if self.count > self.of_last:
            raise ValueError(
                f"deny_window.count ({self.count}) cannot exceed deny_window.of_last "
                f"({self.of_last}); escalation could never fire"
            )
        return self


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
