"""Publica UN mensaje de prueba (contrato de geolocalización) en la cola de Service Bus.

Uso (desde la carpeta del proyecto):
    .venv/bin/python scripts/publish_test_message.py <user_id> [incidente]
    ejemplo: .venv/bin/python scripts/publish_test_message.py abc123 armed_robbery

La cadena de conexión se pide sin mostrarla en pantalla y nunca se imprime.
El timestamp del incidente es "ahora" (UTC).
"""

import asyncio
import getpass
import json
import sys
import uuid
from datetime import UTC, datetime

from azure.servicebus import ServiceBusMessage
from azure.servicebus.aio import ServiceBusClient

DEFAULT_QUEUE = "senti-notificaciones"


async def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("Falta el user_id. Uso: publish_test_message.py <user_id> [incidente]")

    user_id = sys.argv[1]
    incidente = sys.argv[2] if len(sys.argv) > 2 else "armed_robbery"
    connection_string = getpass.getpass("Pega la cadena de conexión (no se verá) y pulsa Enter: ").strip().strip("\"'")
    queue = input(f"Nombre de la cola [{DEFAULT_QUEUE}]: ").strip() or DEFAULT_QUEUE

    payload = {
        "event_id": f"prueba-{uuid.uuid4().hex[:8]}",
        "user_id": user_id,
        "incidente": incidente,
        "latitude": 4.6097,
        "longitude": -74.0817,
        "incident_timestamp": datetime.now(UTC).isoformat(),
        "distance_meters": 120.4,
    }
    async with ServiceBusClient.from_connection_string(connection_string) as client:
        async with client.get_queue_sender(queue) as sender:
            await sender.send_messages(ServiceBusMessage(json.dumps(payload)))
    print(f"Publicado en '{queue}': {json.dumps(payload)}")


if __name__ == "__main__":
    asyncio.run(main())
