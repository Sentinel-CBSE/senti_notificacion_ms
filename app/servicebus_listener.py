"""Worker de fondo: consume la cola de Service Bus y despacha notificaciones.

Semántica de liquidación de cada mensaje:
- éxito                     -> complete
- ContractError (venenoso)  -> dead_letter (reintentar nunca lo arreglaría)
- cualquier otro error      -> abandon (Service Bus lo reentrega; tras MaxDeliveryCount
                               pasa solo a la dead-letter queue)
"""

import asyncio
import logging
from typing import Any

from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver

from app.handlers import process_incident_message
from app.models import ContractError
from app.ports import DeviceRepository, NotificationSender

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 30.0


async def settle_message(
    receiver: Any,
    message: Any,
    repo: DeviceRepository,
    sender: NotificationSender,
) -> None:
    """Procesa un mensaje y lo liquida. Nunca lanza (salvo cancelación)."""
    try:
        await process_incident_message(str(message), repo, sender)
    except ContractError as exc:
        logger.error("Mensaje inválido, a dead-letter", extra={"error": str(exc)[:500]})
        await _safe_settle(receiver.dead_letter_message(message, reason="ContractError", error_description=str(exc)[:1000]))
    except Exception:
        logger.exception("Error procesando mensaje, se reintentará")
        await _safe_settle(receiver.abandon_message(message))
    else:
        await _safe_settle(receiver.complete_message(message))


async def _safe_settle(coro: Any) -> None:
    # Si el lock expiró, Service Bus ya reentregará el mensaje; no debe tumbar el worker.
    try:
        await coro
    except Exception:
        logger.exception("No se pudo liquidar el mensaje en Service Bus")


class ServiceBusListener:
    def __init__(
        self,
        connection_string: str,
        queue_name: str,
        repo: DeviceRepository,
        sender: NotificationSender,
        batch_size: int = 10,
        max_wait_seconds: int = 30,
    ) -> None:
        self._connection_string = connection_string
        self._queue_name = queue_name
        self._repo = repo
        self._sender = sender
        self._batch_size = batch_size
        self._max_wait = max_wait_seconds

    async def run(self) -> None:
        """Bucle infinito con reconexión y backoff exponencial. Termina solo al cancelarse."""
        backoff = 1.0
        while True:
            try:
                await self._consume()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Listener de Service Bus caído, reconectando", extra={"retry_in_s": backoff})
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            else:
                backoff = 1.0

    async def _consume(self) -> None:
        async with ServiceBusClient.from_connection_string(self._connection_string) as client:
            async with client.get_queue_receiver(queue_name=self._queue_name) as receiver:
                logger.info("Escuchando cola", extra={"queue": self._queue_name})
                while True:
                    messages = await receiver.receive_messages(
                        max_message_count=self._batch_size,
                        max_wait_time=self._max_wait,
                    )
                    if messages:
                        await self._handle_batch(receiver, messages)

    async def _handle_batch(self, receiver: ServiceBusReceiver, messages: list[Any]) -> None:
        await asyncio.gather(*(settle_message(receiver, m, self._repo, self._sender) for m in messages))
