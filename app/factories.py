"""Composition root: construye los adaptadores concretos a partir de la configuración.

Hay un único motor de base de datos (SQL Server / Azure SQL), así que ya no hay selección
en tiempo de ejecución. El import de Firebase es diferido: sin worker no se carga.
"""

from app.adapters.sqlserver_repo import SqlServerDeviceRepository
from app.config import Settings
from app.ports import DeviceRepository, NotificationSender


def build_repository(settings: Settings) -> DeviceRepository:
    return SqlServerDeviceRepository(settings.odbc_connection_string())


def build_sender(settings: Settings) -> NotificationSender:
    from app.adapters.firebase_sender import FirebaseNotificationSender, build_firebase_app

    credentials_json = (
        settings.firebase_credentials_json.get_secret_value() if settings.firebase_credentials_json else None
    )
    app = build_firebase_app(settings.firebase_credentials_path, credentials_json)
    return FirebaseNotificationSender(app)
