"""Conversión OpenAPI 3.1 -> 3.0.3 para Azure API Management.

FastAPI + Pydantic v2 generan OpenAPI 3.1 (JSON Schema 2020-12), pero APIM solo soporta
completamente 3.0.x. Este conversor es deliberadamente mínimo: cubre las construcciones
que Pydantic emite y que 3.0 no admite, y LANZA `UnsupportedOpenApiConstruct` ante
cualquier otra en vez de degradar el spec en silencio. No usa red ni escribe archivos.

La red de seguridad es `tests/test_openapi_compat.py`, que valida el spec real contra el
schema oficial de 3.0 con openapi-spec-validator.
"""

from typing import Any

TARGET_VERSION = "3.0.3"

# Mapas cuyas CLAVES son nombres definidos por el usuario (modelos, propiedades, tipos MIME,
# códigos de estado...), no palabras clave de OpenAPI: se recorren solo sus valores.
_NAME_MAPS = frozenset({"properties", "schemas", "paths", "content", "responses", "headers", "mapping", "examples"})

# Claves cuyo valor son DATOS de ejemplo/valor por defecto: nunca se transforman.
_DATA_KEYS = frozenset({"example", "default", "enum", "value"})

# Palabras clave de JSON Schema 2020-12 sin equivalente en 3.0: mejor fallar que perder semántica.
_UNSUPPORTED = frozenset(
    {
        "$defs",
        "prefixItems",
        "unevaluatedProperties",
        "unevaluatedItems",
        "dependentSchemas",
        "dependentRequired",
        "propertyNames",
        "contains",
        "patternProperties",
        "contentEncoding",
        "contentMediaType",
        "webhooks",
        "jsonSchemaDialect",
    }
)

_JSON_TYPES: dict[type, str] = {bool: "boolean", int: "integer", float: "number", str: "string"}


class UnsupportedOpenApiConstruct(ValueError):
    """El spec usa algo de 3.1 que este conversor no sabe traducir a 3.0."""


def downgrade_to_3_0(spec: dict[str, Any]) -> dict[str, Any]:
    """Devuelve una copia del spec en OpenAPI 3.0.3. No modifica el original."""
    converted = _walk(spec, in_name_map=False)
    assert isinstance(converted, dict)
    converted["openapi"] = TARGET_VERSION
    return converted


def _walk(node: Any, *, in_name_map: bool) -> Any:
    if isinstance(node, list):
        return [_walk(item, in_name_map=False) for item in node]
    if not isinstance(node, dict):
        return node

    rebuilt: dict[str, Any] = {}
    for key, value in node.items():
        if not in_name_map and key in _DATA_KEYS:
            rebuilt[key] = value
        elif in_name_map:
            rebuilt[key] = _walk(value, in_name_map=False)
        else:
            rebuilt[key] = _walk(value, in_name_map=key in _NAME_MAPS)

    if in_name_map:
        return rebuilt  # es un mapa de nombres, no un objeto con palabras clave
    return _fix_object(rebuilt)


def _fix_object(obj: dict[str, Any]) -> dict[str, Any]:
    for keyword in _UNSUPPORTED & obj.keys():
        raise UnsupportedOpenApiConstruct(f"'{keyword}' no existe en OpenAPI 3.0 y no hay conversión definida")

    _fix_type_list(obj)
    _fix_nullable_union(obj, "anyOf")
    _fix_nullable_union(obj, "oneOf")
    _fix_const(obj)
    _fix_schema_examples(obj)
    _fix_exclusive_bounds(obj)
    _fix_ref_siblings(obj)
    return obj


def _fix_type_list(obj: dict[str, Any]) -> None:
    """`"type": ["string", "null"]` -> `"type": "string", "nullable": true`."""
    types = obj.get("type")
    if not isinstance(types, list):
        return
    non_null = [t for t in types if t != "null"]
    if len(non_null) != 1:
        raise UnsupportedOpenApiConstruct(f"'type' con varios tipos no nulos no es representable en 3.0: {types}")
    obj["type"] = non_null[0]
    if "null" in types:
        obj["nullable"] = True


def _fix_nullable_union(obj: dict[str, Any], keyword: str) -> None:
    """`anyOf: [X, {"type": "null"}]` -> X con `nullable: true` (envuelto en allOf si X es un $ref)."""
    members = obj.get(keyword)
    if not isinstance(members, list) or not any(_is_null_schema(m) for m in members):
        return

    remaining = [m for m in members if not _is_null_schema(m)]
    if len(remaining) != 1:
        raise UnsupportedOpenApiConstruct(
            f"'{keyword}' con null y {len(remaining)} alternativas no es representable en 3.0"
        )

    del obj[keyword]
    only = remaining[0]
    if "$ref" in only:
        # En 3.0 un $ref no admite claves hermanas: se envuelve en allOf.
        obj["allOf"] = [only]
    else:
        for key, value in only.items():
            obj.setdefault(key, value)
    obj["nullable"] = True


def _is_null_schema(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("type") == "null"


def _fix_const(obj: dict[str, Any]) -> None:
    """`const: X` -> `enum: [X]` (y `type` inferido si falta, como exige el uso habitual de enum)."""
    if "const" not in obj:
        return
    value = obj.pop("const")
    obj["enum"] = [value]
    if "type" not in obj and type(value) in _JSON_TYPES:
        obj["type"] = _JSON_TYPES[type(value)]


def _fix_schema_examples(obj: dict[str, Any]) -> None:
    """`examples: [a, b]` en un schema -> `example: a`.

    El `examples` de un tipo de contenido/parámetro es un MAPA (dict) y ya es válido en 3.0.
    """
    examples = obj.get("examples")
    if isinstance(examples, list):
        del obj["examples"]
        if examples:
            obj.setdefault("example", examples[0])


def _fix_exclusive_bounds(obj: dict[str, Any]) -> None:
    """`exclusiveMinimum: 5` -> `minimum: 5, exclusiveMinimum: true` (idem máximo)."""
    for exclusive, bound in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        value = obj.get(exclusive)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            obj[bound] = value
            obj[exclusive] = True


def _fix_ref_siblings(obj: dict[str, Any]) -> None:
    """3.0 ignora/prohíbe claves junto a `$ref`: `{$ref, description}` -> `{allOf: [$ref], description}`."""
    if "$ref" in obj and len(obj) > 1:
        ref = obj.pop("$ref")
        obj["allOf"] = [{"$ref": ref}, *obj.pop("allOf", [])]
