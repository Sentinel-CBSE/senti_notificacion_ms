"""Comprueba, desde tu máquina, que una cadena de conexión de Service Bus y una cola funcionan.

Uso (desde la carpeta del proyecto):
    .venv/bin/python scripts/check_servicebus.py            # bucle de eventos normal de Python
    .venv/bin/python scripts/check_servicebus.py --uvloop   # el mismo que usa uvicorn en el servicio

La cadena se pide sin mostrarla en pantalla (no queda en el historial de la terminal) y el
script nunca imprime la clave; solo el host y el nombre de la política, que no son secretos.
"""

import asyncio
import getpass
import sys

from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import ServiceBusError

DEFAULT_QUEUE = "senti-notificaciones"


def describe(connection_string: str) -> None:
    """Muestra las partes NO secretas de la cadena, para verificar que es la correcta."""
    parts = dict(p.split("=", 1) for p in connection_string.split(";") if "=" in p)
    print(f"  Host:      {parts.get('Endpoint', '(falta Endpoint)')}")
    print(f"  Política:  {parts.get('SharedAccessKeyName', '(falta SharedAccessKeyName)')}")
    print(f"  Tiene clave: {'sí' if parts.get('SharedAccessKey') else 'NO (falta SharedAccessKey)'}")
    if "EntityPath" in parts:
        print(f"  EntityPath:  {parts['EntityPath']}  <- apunta a una entidad concreta")


async def main() -> None:
    raw = getpass.getpass("Pega la cadena de conexión (no se verá) y pulsa Enter: ")
    connection_string = raw.strip()
    if connection_string != raw or connection_string[:1] in "\"'" or connection_string[-1:] in "\"'":
        print("AVISO: lo pegado traía espacios o comillas en los extremos (se quitaron aquí).")
    queue = input(f"Nombre de la cola [{DEFAULT_QUEUE}]: ").strip() or DEFAULT_QUEUE

    print("\nLo que se va a probar:")
    describe(connection_string.strip("\"'"))
    print(f"  Cola:      {queue}\n")

    try:
        async with ServiceBusClient.from_connection_string(connection_string.strip("\"'")) as client:
            async with client.get_queue_receiver(queue, max_wait_time=5) as receiver:
                messages = await receiver.peek_messages(max_message_count=1)
        print(f"OK: conecta y puede leer la cola '{queue}'. Mensajes visibles: {len(messages)}")
    except ServiceBusError as exc:
        print(f"FALLÓ: {type(exc).__name__}")
        print(f"  {str(exc)[:300]}")
    except Exception as exc:  # cadena mal formada, red, etc.
        print(f"FALLÓ: {type(exc).__name__}")
        print(f"  {str(exc)[:300]}")


if __name__ == "__main__":
    if "--uvloop" in sys.argv:
        import uvloop  # lo instala uvicorn[standard]; es el bucle que usa el servicio

        print("Bucle de eventos: uvloop (como el servicio)")
        asyncio.run(main(), loop_factory=uvloop.new_event_loop)
    else:
        print("Bucle de eventos: asyncio estándar")
        asyncio.run(main())
