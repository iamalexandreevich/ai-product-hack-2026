"""Stage 2 output contract: what the LLM classifier is allowed to say.

`ClassifierOutput` is deliberately narrow — one of three decisions, a
closed set of risk tags, and two free-text fields. `extra="forbid"`
means any field the model hallucinates on top of the schema fails
validation rather than being silently accepted.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from agentgate.classify.client import StructuredOutput

Risk = Literal["exfiltration", "destructive", "privilege", "supply_chain", "injection", "config", "network", "none"]


class ClassifierOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["A", "D", "U"]
    risk: Risk = "none"
    reason: str = ""
    suggest: str = ""


def _strict_schema() -> dict:
    schema = ClassifierOutput.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = ["decision", "risk", "reason", "suggest"]
    return schema


RESPONSE_JSON_SCHEMA: dict = _strict_schema()

DECIDE_STRUCTURED_OUTPUT = StructuredOutput(
    name="agentgate_decision", schema=RESPONSE_JSON_SCHEMA, model=ClassifierOutput
)
