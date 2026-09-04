"""The OpenAPI document the service publishes.

FastAPI builds nearly all of it from the routes and the models, which is the
point: the document cannot describe a route the service does not serve. Three
things FastAPI has no way to express are added here, and nowhere else:

- **Optional authentication.** A localhost bind with no token and no issued
  keys accepts every request, so the bearer requirement is declared at the
  document level as "either nothing or the bearer scheme". FastAPI only ever
  emits per-operation requirements, and only for `Security` dependencies.
- **The request schemas.** `POST /v1/decide` parses and validates its own
  body so that an invalid one comes back as a 200 `ask` instead of a 422
  (see `agentgate.api.app`), which leaves `DecideRequest` referenced by the
  route's documented `requestBody` but by no route signature -- FastAPI
  therefore never collects it.
- **The examples as written.** FastAPI encodes the finished document with
  nulls dropped, which quietly rewrites every example that documents a null
  field -- and `rule_id`, `model` and the per-stage latencies are null in
  most real answers. The declarations on the routes are put back verbatim.

`app.openapi()` and the exported contracts/openapi.yaml are the same
document: the exporter only writes down what this returns.
"""

from typing import Any

from fastapi import FastAPI

from agentgate.api.deps import BEARER_SCHEME
from agentgate.api.schemas import DecideRequest

# An empty requirement alongside the named one is how OpenAPI says "optional".
OPTIONAL_BEARER: list[dict[str, list[str]]] = [{}, {"bearerAuth": []}]

_REF_TEMPLATE = "#/components/schemas/{model}"

# Overview first, then the endpoints, then the schemas they refer to. The
# document is read by people, and FastAPI emits its own field order.
_READING_ORDER = ("openapi", "info", "servers", "security", "tags", "paths", "components")


def install_openapi(app: FastAPI) -> None:
    """Make `app.openapi()` return the published document instead of the raw
    FastAPI one. Generation stays lazy: nothing is built until asked for."""
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = _published(app, generate())
        return app.openapi_schema

    app.openapi = openapi


def _published(app: FastAPI, document: dict[str, Any]) -> dict[str, Any]:
    _restore_declared_examples(app, document)
    _drop_unreachable_validation_errors(document)
    components = {
        "securitySchemes": {"bearerAuth": BEARER_SCHEME},
        "schemas": dict(sorted((_request_schemas() | document["components"]["schemas"]).items())),
    }
    return _in_reading_order(
        document | {"security": OPTIONAL_BEARER, "components": components}
    )


def _drop_unreachable_validation_errors(document: dict[str, Any]) -> None:
    """Remove the 422 from operations no request can provoke one from.

    FastAPI documents a validation error for every operation that takes a
    parameter, whether or not any value could fail one. `GET /v1/profiles/{id}`
    takes an unconstrained string and answers 404 for anything it does not
    recognise, so a documented 422 there describes an answer the service never
    gives -- the same drift this document exists to prevent, pointing the other
    way. `GET /v1/decisions` keeps its 422: `limit` is bounded 1..500 and
    really does reject values outside it.
    """
    for item in document.get("paths", {}).values():
        for operation in item.values():
            responses = operation.get("responses", {})
            if "422" in responses and not _can_reject_a_value(operation):
                del responses["422"]


def _can_reject_a_value(operation: dict[str, Any]) -> bool:
    """True if some parameter of this operation constrains what it accepts."""
    return any(
        _is_constrained(parameter.get("schema", {}))
        for parameter in operation.get("parameters", ())
    )


def _is_constrained(schema: dict[str, Any]) -> bool:
    branches = [b for kind in ("anyOf", "allOf", "oneOf") for b in schema.get(kind, ())]
    if branches:
        return any(_is_constrained(branch) for branch in branches)
    if schema.get("type") not in (None, "string", "null"):
        return True
    return any(
        key in schema
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                    "minLength", "maxLength", "pattern", "enum")
    )


def _restore_declared_examples(app: FastAPI, document: dict[str, Any]) -> None:
    """Copy every example a route declares back over the encoded document."""
    for route in app.routes:
        operation = _operation_of(route, document)
        if operation is None:
            continue
        responses = getattr(route, "responses", None) or {}
        for declared in (
            getattr(route, "openapi_extra", None) or {},
            {"responses": {str(status): body for status, body in responses.items()}},
        ):
            _copy_examples(declared, operation)


def _operation_of(route: Any, document: dict[str, Any]) -> dict[str, Any] | None:
    item = document["paths"].get(getattr(route, "path", ""), {})
    methods = [name.lower() for name in getattr(route, "methods", ())]
    return next((item[name] for name in methods if name in item), None)


def _copy_examples(source: Any, target: Any) -> None:
    if not isinstance(source, dict) or not isinstance(target, dict):
        return
    for key, value in source.items():
        if key == "examples":
            target[key] = value
        else:
            _copy_examples(value, target.get(key))


def _request_schemas() -> dict[str, Any]:
    """`DecideRequest` and everything it is built out of."""
    schema = DecideRequest.model_json_schema(ref_template=_REF_TEMPLATE)
    nested = schema.pop("$defs", {})
    return {"DecideRequest": schema} | nested


def _in_reading_order(document: dict[str, Any]) -> dict[str, Any]:
    ordered = {key: document[key] for key in _READING_ORDER if key in document}
    return ordered | document
