"""Documentación OpenAPI del webhook de Event Grid.

El endpoint recibe el cuerpo como `Request` crudo (para desenvolver la lista y tolerar
tipos de evento desconocidos), así que FastAPI no puede inferir el schema y lo mostraría
como "any". Aquí se declara el `requestBody` real con `openapi_extra` y se registran los
modelos en `components.schemas` para que los `$ref` se resuelvan en /docs.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema

from app.models import (
    FID_EVENT_TYPE,
    VALIDATION_EVENT_TYPE,
    ErrorResponse,
    EventsProcessedResponse,
    FidRegisteredEvent,
    SubscriptionValidationEvent,
    ValidationHandshakeResponse,
)
from app.openapi_compat import downgrade_to_3_0

_REF = "#/components/schemas/{}"

_DOC_MODELS = (
    FidRegisteredEvent,
    SubscriptionValidationEvent,
    ValidationHandshakeResponse,
    EventsProcessedResponse,
    ErrorResponse,
)

EVENT_GRID_REQUEST_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": (
            "Event Grid entrega SIEMPRE una lista. Cada elemento es el handshake de validación "
            "(una sola vez, al crear la suscripción) o un registro/actualización de FID. "
            "Los demás `eventType` se aceptan pero se ignoran."
        ),
        "content": {
            "application/json": {
                "schema": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "oneOf": [
                            {"$ref": _REF.format("FidRegisteredEvent")},
                            {"$ref": _REF.format("SubscriptionValidationEvent")},
                        ],
                        "discriminator": {
                            "propertyName": "eventType",
                            "mapping": {
                                FID_EVENT_TYPE: _REF.format("FidRegisteredEvent"),
                                VALIDATION_EVENT_TYPE: _REF.format("SubscriptionValidationEvent"),
                            },
                        },
                    },
                },
                "examples": {
                    "fid_registered_or_updated": {
                        "summary": "Registro o actualización de FID (entrega real)",
                        "value": [
                            {
                                "id": "b1c2d3",
                                "eventType": FID_EVENT_TYPE,
                                "data": {"user_id": "user-123", "fid": "fid-abc"},
                                "dataVersion": "1.0",
                            }
                        ],
                    },
                    "subscription_validation": {
                        "summary": "Handshake de validación de la suscripción",
                        "value": [
                            {
                                "eventType": VALIDATION_EVENT_TYPE,
                                "data": {"validationCode": "512d38b6-c7b8-40c8-89fe-f46f9e9622b6"},
                            }
                        ],
                    },
                },
            }
        },
    }
}

EVENT_GRID_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Payload que no cumple el contrato (no es lista, JSON inválido, campos faltantes)."},
}


def install_openapi(app: FastAPI) -> None:
    """Sustituye `app.openapi` para registrar los modelos del webhook en components.schemas."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                description=app.description,
                routes=app.routes,
            )
            _, definitions = models_json_schema(
                [(model, "validation") for model in _DOC_MODELS],
                ref_template=_REF.replace("{}", "{model}"),
            )
            schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions.get("$defs", {}))
            # Después de inyectar los componentes (para convertirlos también) y antes de cachear.
            app.openapi_schema = downgrade_to_3_0(schema)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
