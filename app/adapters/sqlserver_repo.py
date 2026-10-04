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

        conn = pyodbc.connect(connection_string, timeout=_LOGIN_TIMEOUT_SECONDS)
        # El timeout de arriba solo cubre el login; esto limita cada query ya conectada
        # (p. ej. el MERGE esperando el HOLDLOCK de otra transacción) para que falle con
        # una excepción normal en vez de colgar el hilo para siempre.
        conn.timeout = 15
        return conn

    return connect


# Una base serverless pausada tarda ~30 s o más en reanudarse (medido: 26 s en Azure) y el
# primer login llega mientras despierta. Con 10 s el login se rendía antes de que terminara.
_LOGIN_TIMEOUT_SECONDS = 30

# Códigos de Azure SQL que indican indisponibilidad temporal (base reanudándose, failover,
# límite de recursos), no un error permanente. Se buscan en el mensaje porque pyodbc los
# reporta con distintas clases de excepción (p. ej. 40613 llega como ProgrammingError).
_TRANSIENT_SQL_CODES = ("(40613)", "(40197)", "(40501)", "(49918)", "(49919)", "(49920)", "(10928)", "(10929)")


def _is_transient(exc: Exception) -> bool:
    """¿El fallo es de conexión/disponibilidad y vale la pena reintentar?

    Los permanentes (tabla inexistente 208, sin acceso a la base 4060, credenciales) NO.
    `OperationalError` es la clase estándar DB-API que pyodbc usa para timeouts y caídas de red;
    se compara por nombre para no importar pyodbc aquí.
    """
    if type(exc).__name__ == "OperationalError":
        return True
    return any(code in str(exc) for code in _TRANSIENT_SQL_CODES)


class SqlServerDeviceRepository:
    def __init__(
        self,
        connection_string: str,
        connect: Callable[[], Any] | None = None,
        startup_wait_seconds: float = 120.0,
        retry_delay_seconds: float = 5.0,
    ) -> None:
        self._connect = connect or _default_connect(connection_string)
        self._startup_wait = startup_wait_seconds
        self._retry_delay = retry_delay_seconds

    async def verify_schema(self) -> None:
        """Comprueba conexión y tabla al arrancar.

        Reintenta (hasta `startup_wait_seconds`) solo ante fallos transitorios, para sobrevivir
        a una base serverless que está reanudándose. Los errores permanentes fallan de inmediato.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._startup_wait
        attempt = 0
        while True:
            attempt += 1
            try:
                await asyncio.to_thread(self._execute, _VERIFY_SCHEMA, ())
                return
            except Exception as exc:
                if not _is_transient(exc) or loop.time() + self._retry_delay >= deadline:
                    raise
                logger.warning(
                    "BD no disponible al arrancar (¿base serverless reanudándose?), reintentando",
                    extra={"attempt": attempt, "retry_in_s": self._retry_delay, "error": str(exc)[:200]},
                )
                await asyncio.sleep(self._retry_delay)

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
