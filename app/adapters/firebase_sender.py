"""Adaptador Firebase Cloud Messaging del puerto NotificationSender.

Es el ÚNICO módulo que conoce `firebase_admin`. Identifica dispositivos por FID
(Firebase Installation ID), nunca por el token FCM clásico.
"""

import asyncio
import json
import logging

import firebase_admin
from firebase_admin import credentials, messaging

logger = logging.getLogger(__name__)


def build_firebase_app(credentials_path: str | None, credentials_json: str | None) -> firebase_admin.App:
    """Crea la app de Firebase. El JSON (Azure) tiene precedencia sobre el archivo (local)."""
    if credentials_json:
        cred = credentials.Certificate(json.loads(credentials_json))
        source = "FIREBASE_CREDENTIALS_JSON"
    elif credentials_path:
        cred = credentials.Certificate(credentials_path)
        source = "FIREBASE_CREDENTIALS_PATH"
    else:
        raise ValueError("Se requiere FIREBASE_CREDENTIALS_JSON o FIREBASE_CREDENTIALS_PATH")
    logger.info("Firebase inicializado", extra={"credentials_source": source})
    return firebase_admin.initialize_app(cred)


class FirebaseNotificationSender:
    def __init__(self, app: firebase_admin.App) -> None:
        self._app = app

    async def send(self, fid: str, title: str, body: str) -> None:
        message = messaging.Message(
            fid=fid,
            notification=messaging.Notification(title=title, body=body),
            # Alerta de robo: entrega inmediata incluso con el dispositivo en Doze.
            android=messaging.AndroidConfig(priority="high"),
        )
        try:
            # messaging.send es síncrono: en un hilo para no bloquear el event loop.
            message_id = await asyncio.to_thread(messaging.send, message, app=self._app)
        except messaging.UnregisteredError:
            # FID que ya no existe (app desinstalada, FID rotado sin actualizar): fallo permanente.
            # Reintentar no lo arregla, así que se registra y el mensaje se consume.
            logger.warning("FID no registrado en FCM, notificación descartada")
            return
        logger.info("Push enviado a FCM", extra={"fcm_message_id": message_id})
