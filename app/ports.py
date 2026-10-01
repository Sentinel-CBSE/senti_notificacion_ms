"""Puertos (arquitectura hexagonal).

La lógica de negocio (`handlers.py`) depende SOLO de estas interfaces. Las
implementaciones concretas (SQL Server, Firebase) viven en `app/adapters/` y se
construyen en un único lugar: `app/factories.py`. En los tests, `DeviceRepository` se
sustituye por un doble en memoria (`tests/fakes.py`); no es una opción de runtime.
"""

from typing import Protocol


class DeviceRepository(Protocol):
    """Almacena el FID vigente de cada usuario (tabla device_tokens)."""

    async def verify_schema(self) -> None:
        """Comprueba conexión y existencia de la tabla; lanza excepción si algo falta."""
        ...

    async def upsert_fid(self, user_id: str, fid: str) -> None:
        """Si existe la fila del usuario actualiza el fid; si no, la crea."""
        ...

    async def get_fid(self, user_id: str) -> str | None:
        """FID vigente del usuario, o None si no tiene ninguno registrado."""
        ...

    async def close(self) -> None: ...


class NotificationSender(Protocol):
    """Envía una notificación push a UN dispositivo (por FID)."""

    async def send(self, fid: str, title: str, body: str) -> None:
        """Lanza excepción si el envío falló y conviene reintentar el mensaje."""
        ...
