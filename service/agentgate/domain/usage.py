"""Token usage of one stage-2 call, parsed from the provider's raw response.

`Usage.from_raw_response` is the one place that reads the OpenAI-compatible
`usage` object -- `prompt_tokens`, `completion_tokens` and, for a reasoning
model, `completion_tokens_details.reasoning_tokens`. A provider that omits
`usage` entirely returns `None`, not zeros: the difference between "the
call cost nothing" and "we don't know what it cost" is the whole point of
docs/superpowers/service/specs/response-cost-reporting.md, and collapsing
it here would erase that distinction before it ever reaches a `Verdict`.

`cost_amount` turns a `Usage` into money at the operator's configured
price (`profiles/schema.py::ModelConfig.price_per_1m_input/output`), or
`None` when the operator has not priced the model -- see the same spec's
"токены или деньги" compromise.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0

    @classmethod
    def from_raw_response(cls, raw: dict) -> "Usage | None":
        usage = raw.get("usage")
        if not isinstance(usage, dict):
            return None
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if not isinstance(prompt, int) or not isinstance(completion, int):
            return None
        details = usage.get("completion_tokens_details")
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        return cls(input_tokens=prompt, output_tokens=completion, reasoning_tokens=reasoning or 0)


def cost_amount(usage: Usage, price_per_1m_input: float | None, price_per_1m_output: float | None) -> float | None:
    if price_per_1m_input is None or price_per_1m_output is None:
        return None
    return (usage.input_tokens * price_per_1m_input + usage.output_tokens * price_per_1m_output) / 1_000_000
