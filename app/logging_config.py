"""Logging estructurado (JSON, una línea por evento) a stdout.

Azure Container Apps captura stdout/stderr y lo envía a Log Analytics sin configuración.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Atributos estándar de LogRecord: todo lo demás vino de `extra={...}` y se incluye en el JSON.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys() | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Uvicorn instala sus propios handlers en texto plano; los redirigimos al nuestro.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True

    # El SDK de Azure es muy verboso en INFO (loguea cada frame AMQP).
    logging.getLogger("azure").setLevel(logging.WARNING)
