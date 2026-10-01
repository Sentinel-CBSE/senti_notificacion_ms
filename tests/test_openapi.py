from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import FID_EVENT_TYPE, VALIDATION_EVENT_TYPE, FidRegisteredEvent, SubscriptionValidationEvent
from tests.fakes import FakeRepository, FakeSender


@pytest.fixture
def spec() -> dict[str, Any]:
    settings = Settings(_env_file=None, servicebus_enabled=False)  # type: ignore[call-arg]
    with TestClient(create_app(settings, repo=FakeRepository(), sender=FakeSender())) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        return response.json()


def _refs(node: Any) -> list[str]:
    """Todos los $ref anidados en un fragmento del spec."""
    if isinstance(node, dict):
        found = [node["$ref"]] if "$ref" in node else []
        return found + [r for v in node.values() for r in _refs(v)]
    if isinstance(node, list):
        return [r for v in node for r in _refs(v)]
    return []


def test_request_body_is_documented_as_list_of_the_two_events(spec: dict[str, Any]) -> None:
    body = spec["paths"]["/notifications/fid"]["post"]["requestBody"]["content"]["application/json"]
    assert body["schema"]["type"] == "array"
    items = body["schema"]["items"]
    assert {r["$ref"].rsplit("/", 1)[1] for r in items["oneOf"]} == {"FidRegisteredEvent", "SubscriptionValidationEvent"}
    assert set(items["discriminator"]["mapping"]) == {FID_EVENT_TYPE, VALIDATION_EVENT_TYPE}
    assert set(body["examples"]) == {"fid_registered_or_updated", "subscription_validation"}


def test_body_is_not_documented_as_any(spec: dict[str, Any]) -> None:
    body_schema = spec["paths"]["/notifications/fid"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema != {} and "items" in body_schema


def test_every_ref_in_the_spec_resolves(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    for ref in _refs(spec["paths"]):
        assert ref.startswith("#/components/schemas/"), ref
        assert ref.rsplit("/", 1)[1] in schemas, f"{ref} no existe en components.schemas"


def test_event_schemas_use_real_contract_field_names(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    fid = schemas["FidRegisteredEvent"]["properties"]
    assert set(fid) >= {"eventType", "data", "dataVersion"}
    # En OpenAPI 3.0 el `const` de 3.1 se expresa como enum de un solo valor.
    assert fid["eventType"]["enum"] == [FID_EVENT_TYPE]
    assert set(schemas["FidRegistrationData"]["properties"]) == {"user_id", "fid"}
    assert schemas["SubscriptionValidationEvent"]["properties"]["eventType"]["enum"] == [VALIDATION_EVENT_TYPE]
    assert set(schemas["SubscriptionValidationData"]["properties"]) == {"validationCode"}


def test_responses_document_both_outcomes_and_errors(spec: dict[str, Any]) -> None:
    responses = spec["paths"]["/notifications/fid"]["post"]["responses"]
    ok = responses["200"]["content"]["application/json"]["schema"]
    assert {r["$ref"].rsplit("/", 1)[1] for r in ok["anyOf"]} == {"ValidationHandshakeResponse", "EventsProcessedResponse"}
    assert set(spec["components"]["schemas"]["ValidationHandshakeResponse"]["properties"]) == {"validationResponse"}
    assert "400" in responses and "401" not in responses


def test_documented_literals_match_runtime_constants() -> None:
    """Si alguien cambia la constante del código, la documentación no puede quedarse atrás."""
    assert get_args(FidRegisteredEvent.model_fields["event_type"].annotation) == (FID_EVENT_TYPE,)
    assert get_args(SubscriptionValidationEvent.model_fields["event_type"].annotation) == (VALIDATION_EVENT_TYPE,)
