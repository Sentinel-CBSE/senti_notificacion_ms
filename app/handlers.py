"""Lógica de negocio. Depende únicamente de los puertos (`app.ports`) y de los modelos.

No importa pyodbc, firebase_admin ni azure.* — eso vive en `app/adapters/`.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.models import (
    ACCEPTED_FID_EVENT_TYPES,
    VALIDATION_EVENT_TYPE,
    IncidentAlertMessage,
    parse_event_grid_payload,
    parse_fid_registration,
    parse_incident_message,
    parse_validation_data,
)
from app.notification_text import build_notification
from app.ports import DeviceRepository, NotificationSender

logger = logging.getLogger(__name__)


# --- Entrada HTTP: eventos de Event Grid ---


async def handle_event_grid_payload(raw_payload: Any, repo: DeviceRepository) -> dict[str, Any]:
    """Procesa una entrega de Event Grid (siempre una lista).

    - Handshake de validación -> devuelve {"validationResponse": <code>}.
    - Registro/actualización de FID -> upsert (el mismo camino para alta y para cambio).
    - Otros tipos de evento se ignoran con un warning.

    Lanza `ContractError` si el payload no cumple el contrato. Los errores de BD se
    propagan tal cual: Event Grid reintentará y el upsert es idempotente.
    """
    events = parse_event_grid_payload(raw_payload)

    for event in events:
        if event.event_type == VALIDATION_EVENT_TYPE:
            code = parse_validation_data(event).validation_code
            logger.info("Handshake de validación de Event Grid")
            return {"validationResponse": code}

    processed = 0
    for event in events:
        if event.event_type in ACCEPTED_FID_EVENT_TYPES:
            data = parse_fid_registration(event)
            await repo.upsert_fid(data.user_id, data.fid)
            logger.info("FID guardado", extra={"user_id": data.user_id, "event_id": event.id})
            processed += 1
        else:
            logger.warning("Tipo de evento ignorado", extra={"event_type": event.event_type})

    return {"processed": processed}


# --- Entrada del worker: mensajes de Service Bus ---
#
# IDEMPOTENCIA (decisión de equipo del 30 de septiembre): NO se implementa.
# `event_id` identifica el ROBO, no la notificación individual: se repite en los N mensajes
# (uno por usuario) que salen del mismo incidente. Una notificación duplicada cuesta muy poco
# frente a mantener un ID de idempotencia y su caché. Si algún día se reconsidera, la clave
# debe ser la combinación (event_id, user_id), NUNCA event_id solo (bloquearía a todos los
# usuarios menos al primero).


@dataclass(frozen=True)
class DispatchResult:
    sent: bool  # False si el usuario no tiene FID registrado


async def dispatch_notification(
    message: IncidentAlertMessage,
    repo: DeviceRepository,
    sender: NotificationSender,
) -> DispatchResult:
    log_ctx = {"event_id": message.event_id, "user_id": message.user_id}

    fid = await repo.get_fid(message.user_id)
    if fid is None:
        logger.warning("Usuario sin FID registrado, no hay a quién notificar", extra=log_ctx)
        return DispatchResult(sent=False)

    title, body = build_notification(message)
    await sender.send(fid, title, body)
    logger.info("Notificación despachada", extra=log_ctx)
    return DispatchResult(sent=True)


async def process_incident_message(
    raw_body: str | bytes,
    repo: DeviceRepository,
    sender: NotificationSender,
) -> DispatchResult:
    """Parsea y despacha. `ContractError` = mensaje venenoso (no sirve reintentar)."""
    message = parse_incident_message(raw_body)
    return await dispatch_notification(message, repo, sender)
