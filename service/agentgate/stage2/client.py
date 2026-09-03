"""OpenAI-compatible chat-completions client for stage 2.

Fail-closed by construction: `classify()` makes exactly one HTTP call
with the caller-configured timeout, never retries, and turns every
failure mode into a `Stage2Error` with a `kind` the caller can key
`ask` off of — see agentgate.stage2.run.run_stage2. `allow` on error is
not representable here: the happy path is the only way to get a
`ClassifierOutput` back.
"""

import json
import os

import httpx
from pydantic import ValidationError

from agentgate.profiles.schema import ModelConfig
from agentgate.stage2.schema import RESPONSE_JSON_SCHEMA, ClassifierOutput


class Stage2Error(Exception):
    """One of the five fail-closed reasons classify() can raise.

    kind is one of: timeout | http | invalid_json | invalid_schema | empty.
    """

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


class LLMClient:
    def __init__(self, name: str, config: ModelConfig, http: httpx.AsyncClient) -> None:
        self.name = name
        self.config = config
        self._http = http

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
            "max_tokens": 300,
        }
        if self.config.structured_output:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agentgate_decision", "strict": True, "schema": RESPONSE_JSON_SCHEMA},
            }
        return body

    async def classify(self, system: str, user: str) -> tuple[ClassifierOutput, dict]:
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

        if resp.status_code >= 400:
            raise Stage2Error("http", f"status {resp.status_code}")

        try:
            raw = resp.json()
        except ValueError as exc:
            raise Stage2Error("invalid_json", "response body is not JSON") from exc

        try:
            content = raw["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise Stage2Error("empty", "no choices in response") from exc

        if not content.strip():
            raise Stage2Error("empty", "empty content")

        try:
            data = json.loads(_strip_fences(content))
        except ValueError as exc:
            raise Stage2Error("invalid_json", content[:200]) from exc

        try:
            return ClassifierOutput.model_validate(data), raw
        except ValidationError as exc:
            raise Stage2Error("invalid_schema", str(exc)[:200]) from exc


def _strip_fences(text: str) -> str:
    """Tolerate a ```json ... ``` fenced body from a model that ignores the instruction not to."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
