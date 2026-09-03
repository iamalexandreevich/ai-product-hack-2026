"""Pydantic v2 request/response models for the /v1/decide API.

These models are the shared contract between the service, the harness
adapters, and the benchmark work streams. Field names, types, and enum
values are exported as JSON Schema into ../../contracts via
scripts/export_contracts.py; keep them in sync.
"""

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

USER_REQUEST_MAX_CHARS = 2048
RAW_MAX_BYTES = 32768
METADATA_MAX_BYTES = 16384


class Tool(str, Enum):
    shell = "shell"
    file_write = "file_write"
    file_read = "file_read"
    network = "network"
    mcp_call = "mcp_call"


class DecisionKind(str, Enum):
    allow = "allow"
    deny = "deny"
    ask = "ask"


class McpArgs(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ActionArgs(BaseModel):
    cwd: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    mcp: McpArgs | None = None


class DecideRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=128)
    harness: str = Field(min_length=1, max_length=64)
    tool: Tool
    raw: str = ""
    args: ActionArgs
    user_request: str
    profile_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw")
    @classmethod
    def _raw_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > RAW_MAX_BYTES:
            raise ValueError(f"raw exceeds {RAW_MAX_BYTES} bytes")
        return v

    @field_validator("user_request")
    @classmethod
    def _truncate_user_request(cls, v: str) -> str:
        if len(v) > USER_REQUEST_MAX_CHARS:
            return v[-USER_REQUEST_MAX_CHARS:]
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_size(cls, v: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
        if size > METADATA_MAX_BYTES:
            raise ValueError(f"metadata exceeds {METADATA_MAX_BYTES} bytes")
        return v

    @model_validator(mode="after")
    def _shell_requires_raw(self) -> "DecideRequest":
        if self.tool is Tool.shell and not self.raw.strip():
            raise ValueError("raw is required for tool=shell")
        return self


class LatencyMs(BaseModel):
    stage1: int | None = None
    stage2: int | None = None
    total: int


class DecideResponse(BaseModel):
    decision: DecisionKind
    reason: str = ""
    suggest: str = ""
    stage: int
    rule_id: str | None = None
    model: str | None = None
    latency_ms: LatencyMs
    cached: bool = False
    decision_id: str
