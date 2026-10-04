# senti_notificacion_ms

Microservicio de notificaciones push de Sentinel. Un solo proceso con dos entradas:

1. **HTTP** — `POST /notifications/fid`: recibe de Azure Event Grid el registro/actualización de FID (Firebase Installation ID) de un dispositivo y lo guarda con un upsert.
2. **Worker** — consume la cola de Service Bus (`SERVICEBUS_QUEUE_NAME`, un mensaje por usuario a notificar, publicado por geolocalización), busca el FID del usuario, arma el texto y envía el push por Firebase Cloud Messaging.

Solo push. Sin email ni SMS.

## Arquitectura (hexagonal)

```
app/
├── main.py                 FastAPI, lifespan, rutas (/notifications/fid, /health)
├── config.py               Settings (Pydantic Settings): única fuente de variables de entorno
├── models.py               Contratos de datos + parseo (ContractError)
├── handlers.py             Lógica de negocio. Solo depende de los puertos
├── ports.py                Puertos: DeviceRepository, NotificationSender (Protocol)
├── factories.py            Composition root: construye los adaptadores desde la configuración
├── servicebus_listener.py  Worker (reconexión, complete / abandon / dead-letter)
├── openapi_docs.py         Documenta el webhook en OpenAPI y sirve el spec en 3.0.3
├── openapi_compat.py       Conversor OpenAPI 3.1 -> 3.0.3 (Azure API Management)
├── logging_config.py       JSON a stdout
└── adapters/
    ├── sqlserver_repo.py   SQL Server / Azure SQL (pyodbc en hilo, MERGE para el upsert)
    └── firebase_sender.py  messaging.Message(fid=...) + messaging.send (un destinatario por mensaje)
db/
├── 00-create-database.sql  Solo local (en Azure la base la crea el portal/Terraform)
└── 01-device-tokens.sql    Única definición del esquema (idempotente, portable a Azure SQL)
```

`handlers.py` no importa `pyodbc`, `firebase_admin` ni `azure.*`.

**Un solo motor de base de datos**: SQL Server en local (contenedor) y Azure SQL en producción son el mismo motor; no hay selección en tiempo de ejecución. El puerto `DeviceRepository` (Protocol) existe para poder testear la lógica de negocio con un doble en memoria (`tests/fakes.py`); no es una opción de runtime.

El esquema lo define solo `db/01-device-tokens.sql`. La app **no** crea tablas: al arrancar ejecuta `verify_schema()` y falla rápido si no hay conexión o falta la tabla.

### Mensaje de la cola Service Bus

Un mensaje por usuario (no una lista de destinatarios). No hay campo `messageType`: geolocalización es el único publicador.

```json
{
  "event_id": "...", "user_id": "...", "incidente": "armed_robbery",
  "latitude": 4.6097, "longitude": -74.0817,
  "incident_timestamp": "2026-09-30T20:05:00Z", "distance_meters": 120.4
}
```

Este servicio arma el texto (`app/notification_text.py`). `incidente` se traduce con `INCIDENT_LABELS` (`armed_robbery` → "Robo armado", `theft` → "Hurto", `burglary` → "Robo a vivienda"); un valor desconocido no falla: se loguea un warning y el título es "Incidente reportado". El cuerpo es, por ejemplo, *"Reportado a aproximadamente 120 metros de tu ubicación, a las 3:05 p. m."*: la distancia se redondea a entero y la hora va en horario de Colombia (UTC-5; un timestamp sin zona horaria se asume UTC). La frase de la distancia vive en una sola función, `build_location_phrase()`.

**Idempotencia: no se implementa**. `event_id` identifica el **robo**, no la notificación individual: se repite en los N mensajes, uno por usuario, de un mismo incidente. Una notificación duplicada cuesta poco frente a mantener un ID de idempotencia y su caché. Si se reconsidera, la clave debe ser `(event_id, user_id)`, nunca `event_id` solo.

Decisiones de comportamiento del worker:

| Situación | Resultado |
|---|---|
| Push enviado | `complete` |
| Mensaje inválido (JSON roto, campo faltante o fuera de rango, contrato anterior) | `dead_letter` (reintentar no lo arregla) |
| Error transitorio (FCM caído, BD caída) | `abandon` → Service Bus reentrega; tras `MaxDeliveryCount` va solo a la DLQ |
| El usuario no tiene FID registrado | `complete` (nada que reintentar) |
| FCM responde `NotRegistered` (FID que ya no existe) | se loguea y `complete`: es permanente, reintentar no lo arregla |

`/health` devuelve 503 si el worker está habilitado pero murió, para que Container Apps reinicie la réplica.

## Evento de registro de FID (entrada HTTP)

El API Gateway publica en Event Grid (tema `senti-eventos-mq`) un evento por cada `POST notification/registerInstallationId` de la app móvil, y Event Grid lo entrega a `POST /notifications/fid`:

```json
[{
  "id": "<guid>", "eventType": "Sentinel.InstallationIdActualizado",
  "subject": "usuarios/<uid>", "eventTime": "<ISO 8601>", "dataVersion": "1.0",
  "data": { "installationId": "<FID>", "userId": "<uid del token de Firebase>" }
}]
```

En `data` se aceptan `fid` o `installationId` para el FID, y `user_id` o `userId` para el usuario. El mismo evento cubre el alta y la rotación de FID (upsert). Se sigue aceptando el nombre de tipo anterior, `Sentinel.Notification.FidRegisteredOrUpdated`. Cualquier otro tipo de evento del tema se ignora (200, sin guardar). La suscripción de Event Grid debe filtrar por `Sentinel.InstallationIdActualizado`.

## Variables de entorno

Se leen de `.env` en local y de variables reales en Azure (ver `.env.example`).

| Variable | Descripción |
|---|---|
| `ENV` | `production` (por defecto) o `development`. `/docs` y `/redoc` solo existen con `development`; `/openapi.json` siempre está disponible. Cualquier otro valor falla al arrancar |
| `DB_HOST`, `DB_PORT` | Servidor SQL (por defecto puerto 1433). En compose, `DB_HOST=sqlserver`; **nunca `localhost` dentro de un contenedor** |
| `DB_USERNAME`, `DB_PASSWORD`, `DB_DATABASE` | Credenciales y base |
| `DB_ENCRYPT` | `true` por defecto |
| `DB_TRUST_SERVER_CERTIFICATE` | `false` por defecto. En local (certificado autofirmado): `true`. En Azure SQL: `false` |
| `SERVICEBUS_ENABLED`, `SERVICEBUS_CONNECTION_STRING`, `SERVICEBUS_QUEUE_NAME` | Worker |
| `FIREBASE_CREDENTIALS_PATH` / `FIREBASE_CREDENTIALS_JSON` | Archivo (local) o contenido del JSON (Azure). Si hay ambos, gana JSON |
| `LOG_LEVEL` | `INFO` por defecto |

El connection string ODBC se arma a partir de las variables `DB_*` (`Settings.odbc_connection_string()`); la contraseña se escapa, así que admite `;`, `{` y `}`.

### Valores por defecto

Si una variable no está definida, `Settings` (`app/config.py`) usa estos defaults. Sin default: la app no arranca si falta.

| Variable | Default |
|---|---|
| `ENV` | `production` |
| `LOG_LEVEL` | `INFO` |
| `DB_PORT` | `1433` |
| `DB_ENCRYPT` | `true` |
| `DB_TRUST_SERVER_CERTIFICATE` | `false` |
| `SERVICEBUS_ENABLED` | `true` |
| `SERVICEBUS_CONNECTION_STRING` | — |
| `SERVICEBUS_QUEUE_NAME` | `senti-notificaciones-mq` |
| `SERVICEBUS_BATCH_SIZE` | `10` |
| `SERVICEBUS_MAX_WAIT_SECONDS` | `30` |
| `FIREBASE_CREDENTIALS_PATH` | — |
| `FIREBASE_CREDENTIALS_JSON` | — |
| `DB_HOST`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DATABASE` | — (obligatorias) |

Si `SERVICEBUS_ENABLED=true` (el default) y falta `SERVICEBUS_CONNECTION_STRING`, o ninguna de las dos variables de Firebase, la app falla al arrancar en vez de levantar con el worker apagado silenciosamente.

## Desarrollo local

Requisitos: Docker, Python 3.12+ (solo para los tests).

### Todo con Docker (camino recomendado)

```bash
cp .env.example .env            # ajusta FIREBASE_CREDENTIALS_FILE al nombre de tu archivo en ./secrets/
docker compose up -d --build
docker compose logs db-init     # debe terminar en: Tabla dbo.device_tokens creada / db-init OK
curl localhost:8000/health
```

Servicios: `sqlserver` (SQL Server 2022) → `db-init` (crea base y tabla y termina; idempotente) → `api` (espera a que `db-init` termine bien). El emulador de Service Bus (`servicebus`) comparte el mismo SQL Server. La cuenta de servicio de Firebase va en `secrets/` (ignorado por git; compose la monta en solo lectura).

`docker compose down` conserva los datos (volumen `sqlserver-data`); `docker compose down -v` los borra.

Al arrancar, el listener puede registrar un par de errores `Connection refused` mientras el emulador termina de iniciar: se reconecta solo con backoff.

> **Correr `uvicorn` directamente en tu máquina** (sin contenedor) exige instalar unixODBC y el driver `msodbcsql18` en tu sistema, porque `pyodbc` no se puede importar sin `libodbc`. Los **tests no lo necesitan**.

### Tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

### Simular Event Grid

```bash
# handshake
curl -X POST localhost:8000/notifications/fid -H 'Content-Type: application/json' \
  -d '[{"eventType":"Microsoft.EventGrid.SubscriptionValidationEvent","data":{"validationCode":"abc"}}]'
# registro / actualización de FID (mismo evento para ambos casos)
curl -X POST localhost:8000/notifications/fid -H 'Content-Type: application/json' \
  -d '[{"id":"1","eventType":"Sentinel.InstallationIdActualizado","data":{"userId":"user-1","installationId":"FID"},"dataVersion":"1.0"}]'
# ver la fila en SQL Server
docker compose exec sqlserver bash -c '/opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -d senti_notificacion -Q "SELECT * FROM dbo.device_tokens"'
```

`/openapi.json` sirve el contrato en OpenAPI 3.0.3 (Azure APIM no soporta 3.1 por completo) y **siempre está disponible**, porque el API Gateway lo importa para configurar los endpoints. `/docs` y `/redoc` (Swagger UI) solo existen con `ENV=development`; en `.env.example` ya viene así para local.

## Despliegue en Azure Container Apps

El mismo código; solo cambian variables de entorno.

1. **Imagen**: build + push a Docker Hub (repositorio privado recomendado; si es privado, el Container App necesita el registry secret). Usa siempre una etiqueta de versión, no `latest`.
   ```bash
   docker build -t <usuario>/senti_notificacion_ms:1 .
   docker push <usuario>/senti_notificacion_ms:1
   ```
2. **Azure SQL**: crea la base y ejecuta `db/01-device-tokens.sql` contra ella (`sqlcmd -S <servidor>.database.windows.net -d <base> -U <user> -P <pass> -G -i db/01-device-tokens.sql`). La app no crea tablas; su usuario solo necesita `db_datareader` + `db_datawriter`.
3. **Container App**: ingress externo puerto 8000, mínimo 1 réplica (el worker debe estar siempre escuchando), probes de liveness/readiness en `/health`.
4. **Variables** (las sensibles como *secrets*, referenciadas con `secretref:`):

   | Variable | Valor |
   |---|---|
   | `DB_HOST` | `<servidor>.database.windows.net` |
   | `DB_PORT` | `1433` |
   | `DB_USERNAME` | usuario SQL |
   | `DB_PASSWORD` | secreto |
   | `DB_DATABASE` | nombre de la base |
   | `DB_ENCRYPT` | `true` |
   | `DB_TRUST_SERVER_CERTIFICATE` | `false` |
   | `SERVICEBUS_ENABLED` | `true` |
   | `SERVICEBUS_CONNECTION_STRING` | secreto |
   | `SERVICEBUS_QUEUE_NAME` | `senti-notificaciones-mq` (default) — confirmar que coincide con el nombre real de la cola en el namespace |
   | `FIREBASE_CREDENTIALS_JSON` | secreto: contenido completo del JSON de la cuenta de servicio (reemplaza a `FIREBASE_CREDENTIALS_PATH`, que no aplica sin volumen montado) |
   | `LOG_LEVEL` | `INFO` |
5. **Cola Service Bus**: `RequiresDuplicateDetection` activo (el servicio no hace su propia idempotencia), `MaxDeliveryCount` y DLQ definidos.
6. **Suscripción de Event Grid**: endpoint tipo webhook `https://<app>.<region>.azurecontainerapps.io/notifications/fid`. El servicio contesta el handshake de validación automáticamente.
7. Los logs salen por stdout en JSON y aparecen en Log Analytics sin configuración adicional.
