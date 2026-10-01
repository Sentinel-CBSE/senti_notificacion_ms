"""Configuración centralizada. Única fuente de verdad de variables de entorno.

Local: se lee de `.env` (python-dotenv, vía pydantic-settings).
Azure: se leen las variables de entorno reales del Container App; `.env` no existe
en la imagen y simplemente se ignora.
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


def _odbc_value(value: str) -> str:
    """Escapa un valor para un connection string ODBC (`;`, `{` y `}` romperían el parseo)."""
    return "{" + value.replace("}", "}}") + "}"


def _yes_no(flag: bool) -> str:
    return "yes" if flag else "no"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Por defecto "production" (falla de forma segura si se olvida definirla): /docs y /redoc
    # solo existen con ENV=development. Un valor distinto de "development" o "production"
    # (p. ej. un typo como "prod") falla al arrancar.
    env: Literal["development", "production"] = "production"
    log_level: str = "INFO"

    # --- SQL Server / Azure SQL (mismo motor en local y en producción) ---
    db_host: str
    db_port: int = 1433
    db_username: str
    db_password: SecretStr
    db_database: str
    db_encrypt: bool = True
    db_trust_server_certificate: bool = False

    # --- Service Bus (worker de fondo) ---
    servicebus_enabled: bool = True
    servicebus_connection_string: SecretStr | None = None
    servicebus_queue_name: str = "senti-notificaciones-mq"
    # le=20: cada mensaje dispara 2 llamadas bloqueantes (BD + Firebase) vía asyncio.to_thread;
    # el pool de hilos en main.py se dimensiona a partir de este tope (ver _size_thread_pool).
    servicebus_batch_size: int = Field(default=10, ge=1, le=20)
    servicebus_max_wait_seconds: int = Field(default=30, ge=1)

    # --- Firebase (se necesita solo si el worker está activo) ---
    firebase_credentials_path: str | None = None
    firebase_credentials_json: SecretStr | None = None

    @model_validator(mode="after")
    def _validate_required_for_enabled_features(self) -> Self:
        # Falla rápido al arrancar: es mejor que el contenedor no levante a que
        # el worker de alertas de robo quede silenciosamente apagado.
        if self.servicebus_enabled:
            if not self.servicebus_connection_string:
                raise ValueError(
                    "SERVICEBUS_ENABLED=true requiere SERVICEBUS_CONNECTION_STRING "
                    "(o pon SERVICEBUS_ENABLED=false para correr solo el endpoint HTTP)"
                )
            if not (self.firebase_credentials_json or self.firebase_credentials_path):
                raise ValueError(
                    "SERVICEBUS_ENABLED=true requiere FIREBASE_CREDENTIALS_JSON "
                    "o FIREBASE_CREDENTIALS_PATH"
                )
        return self

    def odbc_connection_string(self) -> str:
        """Connection string ODBC armado a partir de las variables DB_*. Contiene la contraseña: no loguear."""
        return ";".join(
            [
                f"DRIVER={{{ODBC_DRIVER}}}",
                f"SERVER={self.db_host},{self.db_port}",
                f"DATABASE={_odbc_value(self.db_database)}",
                f"UID={_odbc_value(self.db_username)}",
                f"PWD={_odbc_value(self.db_password.get_secret_value())}",
                f"Encrypt={_yes_no(self.db_encrypt)}",
                f"TrustServerCertificate={_yes_no(self.db_trust_server_certificate)}",
                "Connection Timeout=30",
            ]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # los campos requeridos vienen del entorno
