import hashlib
from enum import Enum
from functools import cached_property
from typing import Any, Literal

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
    price_per_1m_input: float | None = None
    price_per_1m_output: float | None = None

    @model_validator(mode="after")
    def _prices_set_together(self) -> "ModelConfig":
        if (self.price_per_1m_input is None) != (self.price_per_1m_output is None):
            raise ValueError(
                "price_per_1m_input and price_per_1m_output must both be set or both omitted"
            )
        return self


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


class PerTurnChars(BaseModel):
    """Per-role cap on one turn's content, in characters."""

    human: int = Field(default=2048, ge=1)
    assistant: int = Field(default=1500, ge=1)
    toolcall: int = Field(default=1000, ge=1)
    toolresult: int = Field(default=1500, ge=1)


class History(BaseModel):
    """How much of the dialogue reaches the stage-2 prompt.

    Characters, not tokens: the service has no tokenizer, and `user_request`
    is already budgeted in characters. `budget_chars` is measured on the
    JSON-escaped content the prompt emits, so control-heavy tool output
    counts at its rendered size; `per_turn_chars` caps raw characters.
    """

    budget_chars: int = Field(default=12000, ge=1)
    per_turn_chars: PerTurnChars = Field(default_factory=PerTurnChars)

    def cap_for(self, role: str) -> int:
        caps = self.per_turn_chars
        return {
            "human": caps.human,
            "assistant": caps.assistant,
            "toolcall": caps.toolcall,
            "toolresult": caps.toolresult,
        }[role]


class ModelBudget(BaseModel):
    """How much of a tool result reaches the inspect classifier, in the
    operator's units: characters and lines, never tokens."""

    max_chars: int = Field(default=24000, ge=1)
    window_lines: int = Field(default=12, ge=0)
    max_segments: int = Field(default=20, ge=1)
    segment_max_lines: int = Field(default=200, ge=1)


class InspectSettings(BaseModel):
    """How the inspect route judges a tool result beyond stage 1.

    `secrets` defaults on: the detector is deterministic, costs
    milliseconds and needs no model. `classifier` defaults off: `always`
    is the only mode that catches a paraphrased injection and the only
    expensive one.
    """

    classifier: Literal["off", "on-flag", "always"] = "off"
    secrets: Literal["on", "off"] = "on"
    model_budget: ModelBudget = Field(default_factory=ModelBudget)


class Profile(BaseModel):
    """A policy profile as the service loaded it. Server-side configuration; a
    harness never receives this in normal operation. Contains no secret values
    — only the *names* of the environment variables holding API keys."""

    id: str
    allowed_paths: list[str]
    protected_paths: list[str]
    protected_branches: list[str] = Field(default_factory=lambda: ["main", "master"])
    network: Network = Field(default_factory=Network)
    safe_prefixes: list[list[str]] = Field(default_factory=list)
    models: ModelsConfig
    escalation: Escalation = Field(default_factory=Escalation)
    prose: Prose = Field(default_factory=Prose)
    history: History = Field(default_factory=History)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    inspect: InspectSettings = Field(default_factory=InspectSettings)

    @cached_property
    def _hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()

    def profile_hash(self) -> str:
        """This profile's identity, as recorded on every decision.

        Computed once: profiles are loaded from YAML and never mutated.
        ``model_copy`` carries the computed value along with it, so a copy
        with changed fields would report the original's hash -- construct a
        new Profile rather than copying one.
        """
        return self._hash
