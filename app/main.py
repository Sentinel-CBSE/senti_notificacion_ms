"""FastAPI: endpoint HTTP para Event Grid + worker de Service Bus en el mismo proceso.

Arranque:  uvicorn app.main:create_app --factory
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.factories import build_repository, build_sender
from app.handlers import handle_event_grid_payload
from app.logging_config import setup_logging
from app.models import ContractError, EventsProcessedResponse, ValidationHandshakeResponse
from app.openapi_docs import EVENT_GRID_REQUEST_BODY, EVENT_GRID_RESPONSES, install_openapi
from app.ports import DeviceRepository, NotificationSender
from app.servicebus_listener import ServiceBusListener

logger = logging.getLogger(__name__)

# El pool por defecto de asyncio.to_thread (min(32, cpus+4)) es muy chico frente a un lote
# del worker: cada mensaje dispara 2 llamadas bloqueantes (BD + Firebase). Se dimensiona a
# partir de SERVICEBUS_BATCH_SIZE (tope real) + margen para requests HTTP concurrentes.
def _size_thread_pool(batch_size: int) -> int:
    return batch_size * 2 + 10


def create_app(
    settings: Settings | None = None,
    *,
    repo: DeviceRepository | None = None,
    sender: NotificationSender | None = None,
) -> FastAPI:
    """`repo` y `sender` son inyectables para tests; en producción salen de la configuración."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        max_workers = _size_thread_pool(settings.servicebus_batch_size)
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=max_workers))
        logger.info("Thread pool dimensionado", extra={"max_workers": max_workers})

        device_repo = repo or build_repository(settings)
        # Si la primera conexión se cuelga (red o BD no responden), falla el arranque en vez
        # de dejar el contenedor colgado para siempre sin que ningún healthcheck lo detecte.
        await asyncio.wait_for(device_repo.verify_schema(), timeout=20)
        app.state.repo = device_repo
        app.state.listener_task = None

        if settings.servicebus_enabled:
            assert settings.servicebus_connection_string is not None  # garantizado por Settings
            listener = ServiceBusListener(
                connection_string=settings.servicebus_connection_string.get_secret_value(),
                queue_name=settings.servicebus_queue_name,
                repo=device_repo,
                sender=sender or build_sender(settings),
                batch_size=settings.servicebus_batch_size,
                max_wait_seconds=settings.servicebus_max_wait_seconds,
            )
            app.state.listener_task = asyncio.create_task(listener.run(), name="servicebus-listener")
        else:
            logger.warning("Worker de Service Bus deshabilitado (SERVICEBUS_ENABLED=false)")

        logger.info("Servicio iniciado", extra={"db_host": settings.db_host, "db_database": settings.db_database})
        try:
            yield
        finally:
            task: asyncio.Task[None] | None = app.state.listener_task
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await device_repo.close()
            logger.info("Servicio detenido")

    docs_enabled = settings.env != "production"
    app = FastAPI(
        title="senti_notificacion_ms",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json",  # siempre disponible: el API Gateway lo importa para configurar los endpoints
    )
    install_openapi(app)

    @app.get("/health")
    async def health(request: Request) -> Response:
        """Probe de liveness/readiness. Falla (503) si el worker murió, para que Container Apps reinicie."""
        task: asyncio.Task[None] | None = request.app.state.listener_task
        if settings.servicebus_enabled and (task is None or task.done()):
            return JSONResponse({"status": "unhealthy", "reason": "servicebus listener not running"}, 503)
        return JSONResponse({"status": "ok"})

    @app.post(
        "/notifications/fid",
        summary="Webhook de Azure Event Grid: registro/actualización de FID",
        response_model=ValidationHandshakeResponse | EventsProcessedResponse,
        responses=EVENT_GRID_RESPONSES,
        openapi_extra=EVENT_GRID_REQUEST_BODY,
    )
    async def receive_fid_event(request: Request) -> dict[str, Any]:
        """Handshake de validación -> `{"validationResponse": "<code>"}`.
        Registro/actualización de FID -> upsert y `{"processed": n}`."""
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"JSON inválido: {exc}") from exc

        try:
            return await handle_event_grid_payload(payload, request.app.state.repo)
        except ContractError as exc:
            # 400 = payload malo. Cualquier otro error (p. ej. BD caída) sube como 500
            # y Event Grid reintenta; el upsert es idempotente.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
