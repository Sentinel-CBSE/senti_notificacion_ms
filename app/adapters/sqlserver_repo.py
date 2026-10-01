"""Persistencia en SQL Server / Azure SQL Database (mismo motor, local y producción).

pyodbc es síncrono: cada operación corre en un hilo con `asyncio.to_thread` para
no bloquear el event loop. Se abre una conexión por operación; el pooling de ODBC
(activo por defecto) evita el costo de reconectar.

El esquema (tabla device_tokens) NO se crea desde la app: lo define `db/01-device-tokens.sql`,
que se ejecuta con el servicio `db-init` en local y a mano contra Azure SQL.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_VERIFY_SCHEMA = "SELECT TOP 0 user_id, fid, updated_at FROM dbo.device_tokens"

# HOLDLOCK evita la condición de carrera clásica de MERGE (dos upserts concurrentes
# del mismo usuario intentando ambos INSERT y chocando con la PK).
_MERGE = """
MERGE dbo.device_tokens WITH (HOLDLOCK) AS target
USING (SELECT ? AS user_id, ? AS fid) AS src
ON target.user_id = src.user_id
WHEN MATCHED THEN
    UPDATE SET fid = src.fid, updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
    INSERT (user_id, fid, updated_at) VALUES (src.user_id, src.fid, SYSUTCDATETIME());
"""

_SELECT_FID = "SELECT fid FROM dbo.device_tokens WHERE user_id = ?"


def _default_connect(connection_string: str) -> Callable[[], Any]:
    def connect() -> Any:
        import pyodbc  # import diferido: exige libodbc del sistema (presente en la imagen Docker)

        conn = pyodbc.connect(connection_string, timeout=10)
        # timeout=10 de arriba solo cubre el login; esto limita cada query ya conectada
        # (p. ej. el MERGE esperando el HOLDLOCK de otra transacción) para que falle con
        # una excepción normal en vez de colgar el hilo para siempre.
        conn.timeout = 15
        return conn

    return connect


class SqlServerDeviceRepository:
    def __init__(
        self,
        connection_string: str,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self._connect = connect or _default_connect(connection_string)

    async def verify_schema(self) -> None:
        """Falla rápido al arrancar si no hay conexión o la tabla no existe."""
        await asyncio.to_thread(self._execute, _VERIFY_SCHEMA, ())

    async def upsert_fid(self, user_id: str, fid: str) -> None:
        await asyncio.to_thread(self._execute, _MERGE, (user_id, fid))

    async def get_fid(self, user_id: str) -> str | None:
        return await asyncio.to_thread(self._get_fid, user_id)

    async def close(self) -> None:
        return None

    # --- síncrono (corre en hilo) ---

    def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        conn = self._connect()
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _get_fid(self, user_id: str) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute(_SELECT_FID, (user_id,)).fetchone()
        finally:
            conn.close()
        return None if row is None else str(row[0])
