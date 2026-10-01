import copy
import socket
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from app.config import Settings
from app.main import create_app
from app.openapi_compat import UnsupportedOpenApiConstruct, downgrade_to_3_0
from tests.fakes import FakeRepository, FakeSender


def _app() -> Any:
    settings = Settings(_env_file=None, servicebus_enabled=False)  # type: ignore[call-arg]
    return create_app(settings, repo=FakeRepository(), sender=FakeSender())


def _wrap(schema: dict[str, Any]) -> dict[str, Any]:
    """Spec mínimo 3.1 con un schema en components, para probar reglas de una en una."""
    return {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {}, "components": {"schemas": {"X": schema}}}


def _x(spec: dict[str, Any]) -> dict[str, Any]:
    return downgrade_to_3_0(spec)["components"]["schemas"]["X"]  # type: ignore[no-any-return]


# --- El spec REAL de la app ---


def test_served_spec_is_openapi_3_0() -> None:
    with TestClient(_app()) as client:
        spec = client.get("/openapi.json").json()
    assert spec["openapi"] == "3.0.3"


def test_served_spec_validates_against_official_3_0_schema() -> None:
    """La comprobación clave: el JSON que ve APIM cumple el schema oficial de OpenAPI 3.0."""
    with TestClient(_app()) as client:
        spec = client.get("/openapi.json").json()
    validate(spec)  # lanza OpenAPIValidationError si no es válido


def test_served_spec_has_no_31_only_constructs() -> None:
    def scan(node: Any, path: str = "") -> list[str]:
        problems: list[str] = []
        if isinstance(node, dict):
            if "const" in node:
                problems.append(f"const en {path}")
            if isinstance(node.get("type"), list):
                problems.append(f"type lista en {path}")
            for key in ("anyOf", "oneOf"):
                if any(isinstance(m, dict) and m.get("type") == "null" for m in node.get(key, [])):
                    problems.append(f"{key} con null en {path}")
            for key, value in node.items():
                if key != "examples":  # el `examples` de tipo de contenido es un mapa válido en 3.0
                    problems += scan(value, f"{path}/{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                problems += scan(item, f"{path}[{i}]")
        return problems

    with TestClient(_app()) as client:
        assert scan(client.get("/openapi.json").json()) == []


def test_nullable_fields_survive_conversion() -> None:
    with TestClient(_app()) as client:
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert schemas["FidRegisteredEvent"]["properties"]["dataVersion"]["nullable"] is True
    assert schemas["FidRegisteredEvent"]["properties"]["dataVersion"]["type"] == "string"


def test_request_examples_are_preserved() -> None:
    with TestClient(_app()) as client:
        spec = client.get("/openapi.json").json()
    examples = spec["paths"]["/notifications/fid"]["post"]["requestBody"]["content"]["application/json"]["examples"]
    assert set(examples) == {"fid_registered_or_updated", "subscription_validation"}
    # Los datos del ejemplo no se tocan aunque contengan palabras "raras".
    assert examples["subscription_validation"]["value"][0]["data"]["validationCode"]


def test_generation_needs_no_network_and_writes_no_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Regresión del problema que descartó openapi-downgrade: nada de red ni archivos en cwd."""

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("la generación del OpenAPI intentó abrir una conexión de red")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.chdir(tmp_path)
    assert _app().openapi()["openapi"] == "3.0.3"
    assert list(tmp_path.iterdir()) == []


# --- Reglas del conversor, una por una ---


def test_nullable_primitive_is_merged_and_keeps_siblings() -> None:
    out = _x(_wrap({"anyOf": [{"type": "string"}, {"type": "null"}], "title": "Id", "default": None}))
    assert out == {"type": "string", "nullable": True, "title": "Id", "default": None}


def test_nullable_ref_is_wrapped_in_allof() -> None:
    out = _x(_wrap({"anyOf": [{"$ref": "#/components/schemas/Y"}, {"type": "null"}]}))
    assert out == {"allOf": [{"$ref": "#/components/schemas/Y"}], "nullable": True}


def test_type_list_with_null() -> None:
    assert _x(_wrap({"type": ["integer", "null"]})) == {"type": "integer", "nullable": True}


def test_const_becomes_single_value_enum_with_inferred_type() -> None:
    assert _x(_wrap({"const": "a"})) == {"enum": ["a"], "type": "string"}
    assert _x(_wrap({"const": 3, "type": "integer"})) == {"enum": [3], "type": "integer"}


def test_schema_examples_list_becomes_example() -> None:
    assert _x(_wrap({"type": "string", "examples": ["a", "b"]})) == {"type": "string", "example": "a"}


def test_numeric_exclusive_bounds() -> None:
    out = _x(_wrap({"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 10}))
    assert out == {"type": "integer", "minimum": 0, "exclusiveMinimum": True, "maximum": 10, "exclusiveMaximum": True}


def test_ref_with_siblings_is_wrapped() -> None:
    out = _x(_wrap({"properties": {"p": {"$ref": "#/components/schemas/Y", "description": "d"}}}))
    assert out["properties"]["p"] == {"allOf": [{"$ref": "#/components/schemas/Y"}], "description": "d"}


def test_property_names_that_look_like_keywords_are_not_touched() -> None:
    out = _x(_wrap({"type": "object", "properties": {"const": {"type": "string"}, "anyOf": {"type": "integer"}}}))
    assert set(out["properties"]) == {"const", "anyOf"}
    assert "enum" not in out["properties"]["const"]


def test_example_and_default_data_are_not_transformed() -> None:
    payload = {"const": 1, "anyOf": [1, {"type": "null"}]}
    out = _x(_wrap({"type": "object", "example": payload, "default": payload}))
    assert out["example"] == payload and out["default"] == payload


def test_input_is_not_mutated_and_version_is_set() -> None:
    spec = _wrap({"const": "a"})
    original = copy.deepcopy(spec)
    result = downgrade_to_3_0(spec)
    assert spec == original
    assert result["openapi"] == "3.0.3"


@pytest.mark.parametrize(
    "schema",
    [
        {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},  # null + 2 alternativas
        {"type": ["string", "integer"]},
        {"prefixItems": [{"type": "string"}]},
        {"patternProperties": {"^a": {"type": "string"}}},
        {"$defs": {"a": {}}},
    ],
)
def test_unsupported_constructs_fail_loudly_instead_of_degrading(schema: dict[str, Any]) -> None:
    with pytest.raises(UnsupportedOpenApiConstruct):
        downgrade_to_3_0(_wrap(schema))
