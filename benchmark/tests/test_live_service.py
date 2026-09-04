"""Optional integration test against a real AgentGate instance.

Skipped by default (``addopts = -m 'not live'``). Run it explicitly with a service
listening on ``SECURITY_SERVICE_URL``:

    SECURITY_SERVICE_URL=http://127.0.0.1:8400 uv run pytest -m live
"""

from __future__ import annotations

import asyncio
import os

import pytest

from client.security_service import SecurityServiceClient
from config import service_config_from_env
from dataset.loader import load_dataset
from evaluator.scorer import score_case
from schemas.result import ServiceResultType
from tests.conftest import DATASET_PATH

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def config():
    if not (os.getenv("SECURITY_SERVICE_URL") or os.getenv("AGENTGATE_URL")):
        pytest.skip("SECURITY_SERVICE_URL is not set")
    return service_config_from_env(timeout_s=30.0)


def test_service_is_healthy(config):
    async def scenario():
        async with SecurityServiceClient(config) as client:
            return await client.healthz()

    healthy, _ = asyncio.run(scenario())
    assert healthy, "the service did not answer /healthz with HTTP 200"


def test_smoke_one_case_per_category(config):
    cases = [c for c in load_dataset(DATASET_PATH) if c.difficulty.value == "easy"]
    assert cases, "no easy cases in the dataset"

    async def scenario():
        async with SecurityServiceClient(config) as client:
            return [
                await client.evaluate(case.human_req, case.assistant_tool_call, session_id=None)
                for case in cases
            ]

    responses = asyncio.run(scenario())

    for case, response in zip(cases, responses, strict=True):
        # The contract requires a decision on every request, never an HTTP error.
        assert response.result_type is not ServiceResultType.ERROR, f"{case.id}: {response.error}"
        assert response.contract_violation is None, f"{case.id}: {response.contract_violation}"
        outcome = score_case(case, response)
        assert outcome.score in (0, 1)
