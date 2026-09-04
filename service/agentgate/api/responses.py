"""Response models of the read endpoints, and of the one error body.

They live beside `agentgate.api.schemas` rather than inside it because
`DecisionListResponse` is built out of `DecisionRecord`, and the module that
defines `DecisionRecord` already imports `schemas` -- putting the page there
would close the cycle. `schemas` stays the leaf every layer may depend on.

Their names are the names external clients generate types from, so they are
the names contracts/openapi.yaml has always published.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentgate.engine.decision import DecisionRecord


class DecisionListResponse(BaseModel):
    """Response of `GET /v1/decisions`: a page of stored decisions plus a cursor for the next page."""

    items: list[DecisionRecord] = Field(description="Stored decisions, newest first.")
    next_before: str | None = Field(
        default=None,
        description=(
            "Cursor for the next page: pass it back as `before`. `null` on the last page."
        ),
    )


class Health(BaseModel):
    """Response of `GET /healthz`. Always delivered with HTTP 200."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "degraded"] = Field(
        description="`ok`, or `degraded` when the database probe fails."
    )
    db: bool = Field(description="Result of the Postgres liveness probe.")
    llm: str | None = Field(
        description="Reserved for the active LLM endpoint's status; currently always null."
    )
    git_sha: str | None = Field(
        description=(
            "Full commit SHA the running image was built from; `null` when the image "
            "was built without it (local builds)."
        )
    )


class Error(BaseModel):
    """Error body. Only HTTP 401 (a missing/invalid/expired/revoked
    credential) and HTTP 404 use it; every other failure is an HTTP 200 `ask`."""

    detail: str
