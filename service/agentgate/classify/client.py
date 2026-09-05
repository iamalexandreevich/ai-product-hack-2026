"""OpenAI-compatible chat-completions client for stage 2.

Fail-closed by construction: `classify()` makes exactly one HTTP call
with the caller-configured timeout, never retries, and turns every
failure mode into a `Stage2Error` with a `kind` the caller can key
`ask` off of — see agentgate.classify.llm.LLMClassifier. `allow` on
error is not representable here: the happy path is the only way to get a
`ClassifierOutput` back.
"""

import json
import os
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from agentgate.domain.usage import Usage
from agentgate.profiles.schema import ModelConfig


class Stage2Error(Exception):
    """One of the five fail-closed reasons classify() can raise.

    kind is one of: timeout | http | invalid_json | invalid_schema | empty.
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class StructuredOutput:
    """The name, JSON schema and Pydantic model for one stage 2 call's
    structured output.

    `name` is the `json_schema.name` the request declares -- two callers
    with different schemas must not share it, or an OpenAI-compatible
    provider that caches by name could hand one caller the other's shape.
    `classify/schema.py` builds the decide value, `inspect/classify.py` its
    own; `LLMClient` takes one explicitly rather than defaulting to either.
    """

    name: str
    schema: dict
    model: type[BaseModel]
    # Enough for one decide answer; a caller whose answer is a list (the
    # inspect spans) raises it explicitly.
    max_tokens: int = 300


class LLMClient:
    """Talks to one OpenAI-compatible chat-completions endpoint."""

    def __init__(
        self, name: str, config: ModelConfig, http: httpx.AsyncClient, structured_output: StructuredOutput,
    ) -> None:
        self.name = name
        self.config = config
        self._http = http
        self._structured_output = structured_output

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.config.api_key_env:
            key = os.environ.get(self.config.api_key_env, "")
            if key:
                headers["authorization"] = f"Bearer {key}"
        return headers

    def _body(self, system: str, user: str) -> dict:
        body: dict = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": self._structured_output.max_tokens,
        }
        if self.config.structured_output:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": self._structured_output.name, "strict": True, "schema": self._structured_output.schema,
                },
            }
        return body

    async def classify(self, system: str, user: str) -> tuple[BaseModel, dict, Usage | None]:
        """One request, no retries. Raises Stage2Error on every failure path."""
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = await self._http.post(
                url,
                json=self._body(system, user),
                headers=self._headers(),
                timeout=self.config.timeout_ms / 1000,
            )
        except httpx.TimeoutException as exc:
            raise Stage2Error("timeout", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise Stage2Error("http", str(exc)) from exc

        if resp.status_code != 200:
            # Not just >= 400: a redirect or any other non-200 status is not
            # a completions response either, and letting it fall through to
            # the JSON parser below would misreport it as invalid_json.
            raise Stage2Error("http", f"status {resp.status_code}")

        try:
            raw = resp.json()
        except ValueError as exc:
            raise Stage2Error("invalid_json", "response body is not JSON") from exc

        try:
            content = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise Stage2Error("empty", "no choices in response") from exc

        if content is None:
            content = ""
        elif not isinstance(content, str):
            # Some OpenAI-compatible providers emit content as a list of
            # typed parts (e.g. [{"type": "text", "text": "..."}]) instead
            # of a plain string. That's a response that doesn't match the
            # shape we require, not an empty one — content.strip() below
            # would raise AttributeError and escape as "unexpected" instead
            # of a proper Stage2Error kind.
            raise Stage2Error("invalid_schema", "content is not a string")

        if not content.strip():
            raise Stage2Error("empty", "empty content")

        try:
            data = json.loads(_strip_fences(content))
        except ValueError as exc:
            raise Stage2Error("invalid_json", content[:200]) from exc

        try:
            output = self._structured_output.model.model_validate(data)
        except ValidationError as exc:
            raise Stage2Error("invalid_schema", str(exc)[:200]) from exc
        return output, raw, Usage.from_raw_response(raw)


def _strip_fences(text: str) -> str:
    """Tolerate a ```json ... ``` fenced body from a model that ignores the instruction not to."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
