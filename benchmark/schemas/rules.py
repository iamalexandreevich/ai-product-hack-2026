"""Client policy carried by v3 /v1/decide requests."""

import hashlib
import json
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    level: str = Field(default="custom", max_length=32)
    allow: list[str] = Field(default_factory=list)
    ask: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_limits(self) -> Self:
        patterns = self.allow + self.ask + self.deny
        if self.version != 1:
            raise ValueError("only rules version 1 is supported")
        if len(patterns) > 500 or any(len(p) > 200 for p in patterns):
            raise ValueError("rules exceed 500 patterns or 200 characters per pattern")
        if sum(len(p.encode("utf-8", "surrogatepass")) for p in patterns) > 16384:
            raise ValueError("rules exceed 16384 UTF-8 bytes")
        return self

    def digest(self) -> str:
        """Benchmark snapshot identity; includes labels, unlike the service cache key."""
        wire = json.dumps(self.model_dump(), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(wire.encode()).hexdigest()


def load_rules(path: str | Path) -> RuleSet:
    return RuleSet.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
