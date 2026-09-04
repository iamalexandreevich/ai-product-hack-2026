import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from agentgate.api.schemas import DecideRequest, DecideResponse

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load_exporter():
    """Import scripts/export_openapi.py, which is not on the package path."""
    spec = importlib.util.spec_from_file_location(
        "export_openapi", SCRIPTS / "export_openapi.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exporter():
    return _load_exporter()


@pytest.fixture(scope="module")
def committed_openapi():
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def generated_openapi(exporter):
    return exporter.build_document()


def test_request_schema_matches_contract():
    committed = json.loads((CONTRACTS / "decide_request.schema.json").read_text())
    assert committed == DecideRequest.model_json_schema()


def test_response_schema_matches_contract():
    committed = json.loads((CONTRACTS / "decide_response.schema.json").read_text())
    assert committed == DecideResponse.model_json_schema()


def test_openapi_yaml_matches_what_the_app_generates(committed_openapi, generated_openapi):
    """The committed document must equal the one the assembled service serves.

    Nothing about the contract is authored twice: change a route or a model
    without regenerating and this fails.
    """
    assert committed_openapi == generated_openapi


def test_openapi_publishes_one_schema_per_model(generated_openapi):
    """A model whose validation and serialization shapes differ is split by
    pydantic into `<name>-Input`/`<name>-Output`, silently renaming a type
    external clients generate from."""
    split = [name for name in generated_openapi["components"]["schemas"] if "-" in name]
    assert split == []


def test_openapi_refs_all_resolve(committed_openapi):
    """Every local $ref points at something that exists in the document."""
    schemas = committed_openapi["components"]["schemas"]
    refs: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str):
                refs.append(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(committed_openapi)
    assert refs, "document has no $refs at all — generation is broken"
    for ref in refs:
        prefix = "#/components/schemas/"
        assert ref.startswith(prefix), f"unexpected $ref form: {ref}"
        assert ref[len(prefix) :] in schemas, f"dangling $ref: {ref}"


def test_openapi_decide_examples_validate_against_the_models(committed_openapi):
    """Documented examples must be real: requests parse, responses parse.

    An example that the models would reject teaches an external developer
    the wrong contract, so it is a test failure, not a typo.
    """
    decide = committed_openapi["paths"]["/v1/decide"]["post"]
    requests = decide["requestBody"]["content"]["application/json"]["examples"]
    responses = decide["responses"]["200"]["content"]["application/json"]["examples"]

    assert len(requests) >= 3
    assert len(responses) >= 3

    for name, example in requests.items():
        assert DecideRequest.model_validate(example["value"]) is not None, name
    for name, example in responses.items():
        assert DecideResponse.model_validate(example["value"]) is not None, name

    # Every request example has a response example of the same name.
    assert set(requests) <= set(responses)

    decisions = {name: e["value"]["decision"] for name, e in responses.items()}
    assert {"allow", "deny", "ask"} <= set(decisions.values())

    # deny always carries both reason and suggest.
    for name, example in responses.items():
        if example["value"]["decision"] == "deny":
            assert example["value"]["reason"], name
            assert example["value"]["suggest"], name


def test_openapi_documents_every_v1_endpoint(committed_openapi):
    assert set(committed_openapi["paths"]) == {
        "/v1/decide",
        "/v1/decisions",
        "/v1/profiles/{id}",
        "/healthz",
    }


def test_no_endpoint_is_marked_provisional(committed_openapi):
    """All four routes are implemented — nothing in the document may still
    describe one as unbuilt."""
    document = yaml.safe_dump(committed_openapi).lower()
    assert "provisional" not in document
    assert "not yet implemented" not in document


def test_openapi_auth_covers_api_keys(committed_openapi):
    """The bearer scheme must describe the additive API-key credential, not only the static token."""
    auth = committed_openapi["components"]["securitySchemes"]["bearerAuth"]["description"]
    assert "agk_" in auth
    assert "keys create" in auth


def test_openapi_bearer_scheme_is_optional(committed_openapi):
    """Optional auth is `security: [{}, {bearerAuth: []}]` — the empty entry matters."""
    security = committed_openapi["security"]
    assert {} in security
    assert {"bearerAuth": []} in security
    assert "bearerAuth" in committed_openapi["components"]["securitySchemes"]
