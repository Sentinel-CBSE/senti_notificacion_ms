"""Contratos de datos (Pydantic) y funciones de parseo.

Los errores de contrato se convierten en `ContractError` para que quien llama
(la ruta HTTP o el worker) decida qué hacer sin conocer Pydantic.
"""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# Tipo de evento que publica el API Gateway (convención del equipo: Sentinel.<Algo>Actualizado).
FID_EVENT_TYPE = "Sentinel.InstallationIdActualizado"
# Nombre anterior del contrato; nadie lo publica hoy, pero se sigue aceptando por compatibilidad.
LEGACY_FID_EVENT_TYPE = "Sentinel.Notification.FidRegisteredOrUpdated"
ACCEPTED_FID_EVENT_TYPES = frozenset({FID_EVENT_TYPE, LEGACY_FID_EVENT_TYPE})
VALIDATION_EVENT_TYPE = "Microsoft.EventGrid.SubscriptionValidationEvent"


class ContractError(ValueError):
    """El payload recibido no cumple el contrato esperado."""


# --- Azure Event Grid (entrada HTTP) ---


class EventGridEvent(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None
    event_type: str = Field(alias="eventType")
    data: dict[str, Any] = Field(default_factory=dict)
    data_version: str | None = Field(default=None, alias="dataVersion")


class SubscriptionValidationData(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    validation_code: str = Field(alias="validationCode", min_length=1)


class FidRegistrationData(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    # validation_alias (no "alias"): acepta tanto user_id como userId según cómo lo arme la
    # política del API Gateway, pero el schema documentado en /openapi.json sigue mostrando
    # el nombre canónico "user_id" (alias solo afectaría también a la salida/doc).
    user_id: str = Field(min_length=1, validation_alias=AliasChoices("user_id", "userId"))
    # El gateway reenvía el cuerpo de la app móvil tal cual, cuyo campo se llama `installationId`.
    fid: str = Field(min_length=1, validation_alias=AliasChoices("fid", "installationId"))


# --- Modelos SOLO para documentación OpenAPI ---
# El endpoint sigue desenvolviendo la lista a mano (y tolera tipos de evento desconocidos);
# estos modelos únicamente describen los dos eventos posibles en /docs. Reutilizan los
# modelos `data` reales para que documentación y validación no diverjan.


class SubscriptionValidationEvent(BaseModel):
    """Handshake que Azure envía UNA vez al crear la suscripción del webhook."""

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    event_type: Literal["Microsoft.EventGrid.SubscriptionValidationEvent"] = Field(alias="eventType")
    data: SubscriptionValidationData


class FidRegisteredEvent(BaseModel):
    """Registro o actualización de FID. El mismo evento cubre el alta y la rotación."""

    model_config = ConfigDict(populate_by_name=True)

    id: str | None = None
    event_type: Literal["Sentinel.InstallationIdActualizado"] = Field(alias="eventType")
    data: FidRegistrationData
    data_version: str | None = Field(default=None, alias="dataVersion")


class ValidationHandshakeResponse(BaseModel):
    """Respuesta al handshake: repite el `validationCode` recibido."""

    model_config = ConfigDict(populate_by_name=True)

    validation_response: str = Field(alias="validationResponse")


class EventsProcessedResponse(BaseModel):
    """Respuesta a una entrega normal: cuántos eventos de FID se guardaron."""

    processed: int


class ErrorResponse(BaseModel):
    detail: str


_event_list_adapter = TypeAdapter(list[EventGridEvent])


def parse_event_grid_payload(raw: Any) -> list[EventGridEvent]:
    """Event Grid entrega SIEMPRE una lista de eventos."""
    try:
        return _event_list_adapter.validate_python(raw)
    except ValidationError as exc:
        raise ContractError(f"Payload de Event Grid inválido: {exc}") from exc


def parse_validation_data(event: EventGridEvent) -> SubscriptionValidationData:
    try:
        return SubscriptionValidationData.model_validate(event.data)
    except ValidationError as exc:
        raise ContractError(f"Evento de validación inválido: {exc}") from exc


def parse_fid_registration(event: EventGridEvent) -> FidRegistrationData:
    try:
        return FidRegistrationData.model_validate(event.data)
    except ValidationError as exc:
        raise ContractError(f"Evento de registro de FID inválido: {exc}") from exc


# --- Cola Service Bus (SERVICEBUS_QUEUE_NAME, entrada del worker) ---
# Contrato final (publica geolocalización): UN mensaje por usuario a notificar.
# Es el único tipo de evento de la cola, así que no hay campo discriminador (`messageType`).


class IncidentAlertMessage(BaseModel):
    # populate_by_name: acepta tanto el alias (payload real, camelCase) como el nombre del
    # campo (snake_case), por si algún publicador manda uno u otro.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, populate_by_name=True)

    event_id: str = Field(min_length=1, alias="eventId")  # identifica el ROBO, no la notificación (ver handlers.py)
    user_id: str = Field(min_length=1, alias="userId")
    incidente: str = Field(min_length=1, alias="incidentType")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # ISO 8601 o epoch (segundos o milisegundos, Pydantic distingue solo); sin zona horaria se asume UTC
    incident_timestamp: datetime = Field(alias="incidentTimestamp")
    distance_meters: float = Field(ge=0, alias="distanceMeters")


def parse_incident_message(raw: str | bytes) -> IncidentAlertMessage:
    try:
        return IncidentAlertMessage.model_validate(json.loads(raw))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContractError(f"El mensaje no es JSON válido: {exc}") from exc
    except ValidationError as exc:
        raise ContractError(f"Mensaje de incidente inválido: {exc}") from exc
