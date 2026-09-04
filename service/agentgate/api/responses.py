"""Response models of the read endpoints.

They live beside `agentgate.api.schemas` rather than inside it because
`DecisionsPage` is built out of `DecisionView`, and the module that defines
`DecisionView` already imports `schemas` -- putting the page there would
close the cycle. `schemas` stays the leaf every layer may depend on.

Both shapes are documented in contracts/openapi.yaml (`DecisionListResponse`
and `Health`), which is the source of truth for their field names and types.
"""

from typing import Literal

from pydantic import BaseModel

from agentgate.engine.decision import DecisionView


class DecisionsPage(BaseModel):
    items: list[DecisionView]
    next_before: str | None = None


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    db: bool
    llm: str | None = None
