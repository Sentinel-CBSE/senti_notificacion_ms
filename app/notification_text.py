"""Construcción del texto de la notificación a partir del incidente recibido."""

import logging
from datetime import UTC, datetime, timedelta, timezone

from app.models import IncidentAlertMessage

logger = logging.getLogger(__name__)

INCIDENT_LABELS: dict[str, str] = {
    "armed_robbery": "Robo armado",
    "theft": "Hurto",
    "burglary": "Robo a vivienda",
}
FALLBACK_TITLE = "Incidente reportado"

# Colombia no tiene horario de verano: UTC-5 fijo, sin depender de la base de zonas horarias del sistema.
COLOMBIA_TZ = timezone(timedelta(hours=-5), "COT")


def build_location_phrase(distance_meters: float) -> str:
    """Solo la parte de la distancia. Si cambia el redondeo o se quita la distancia, se cambia aquí."""
    meters = int(distance_meters + 0.5)  # mitad hacia arriba (round() de Python redondea 2.5 a 2)
    unit = "metro" if meters == 1 else "metros"
    return f"a aproximadamente {meters} {unit} de tu ubicación"


def _format_time(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    local = moment.astimezone(COLOMBIA_TZ)
    suffix = "a. m." if local.hour < 12 else "p. m."
    return f"{local.hour % 12 or 12}:{local.minute:02d} {suffix}"


def build_notification(message: IncidentAlertMessage) -> tuple[str, str]:
    """Devuelve (title, body). Un incidente desconocido no falla: título genérico y warning."""
    title = INCIDENT_LABELS.get(message.incidente)
    if title is None:
        logger.warning(
            "Tipo de incidente desconocido, se usa texto genérico",
            extra={"incidente": message.incidente[:100], "event_id": message.event_id},
        )
        title = FALLBACK_TITLE
    body = f"Reportado {build_location_phrase(message.distance_meters)}, a las {_format_time(message.incident_timestamp)}"
    if not body.endswith("."):  # "a. m."/"p. m." ya terminan en punto
        body += "."
    return title, body
