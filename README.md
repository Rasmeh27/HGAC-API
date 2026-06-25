# Backend HGAC PoC

Backend de prueba de concepto para control de cruce vehicular portuario en HGAC.
Integra varios subsistemas:

- **LPR** — captura desde cámara (webcam o RTSP) + Plate Recognizer.
- **BioStar 2** — verificación de chofer.
- **RNTT** — consulta del registro nacional vía portal (Selenium aislado; PoC usa stub).
- **Ignition** — temporalmente vía archivos JSON; el backend ya expone REST para consumo futuro.

El objetivo de esta PoC es demostrar el flujo completo de evaluación de cruce
(`AUTHORIZED` / `REJECTED` / `NEEDS_MANUAL_REVIEW`) de forma modular y mantenible.

---

## Estructura del proyecto

```
backend/
├── app/
│   ├── main.py                 # Punto de entrada FastAPI
│   ├── core/
│   │   ├── config.py           # Settings cargados desde .env
│   │   ├── errors.py           # Jerarquía de excepciones del dominio
│   │   └── logging.py          # Configuración de loguru
│   ├── api/
│   │   ├── dependencies.py     # Inyección de dependencias
│   │   ├── schemas.py          # Request/Response Pydantic
│   │   └── routes/
│   │       ├── health_routes.py
│   │       ├── lpr_routes.py        # legacy /lpr/read, /lpr/debug/snapshot
│   │       ├── lpr_reads_routes.py  # módulo LPR formal: POST /api/v1/lpr/reads
│   │       ├── camera_routes.py     # API de cámara para Ignition
│   │       ├── biostar_routes.py
│   │       ├── rntt_routes.py
│   │       └── crossing_routes.py
│   ├── integrations/
│   │   ├── camera/             # CameraProvider + webcam + rtsp + sesión persistente
│   │   ├── lpr/                # Plate Recognizer + motor LPR (engine/detector/easyocr)
│   │   ├── biostar/            # Cliente BioStar 2 + servicio + modelos
│   │   ├── rntt/               # Cliente RNTT (stub o Selenium) + servicio
│   │   └── ignition/           # IgnitionJsonWriter + modelos
│   └── modules/
│       ├── camera/             # CameraService + registry + snapshot_storage + stream_manager
│       ├── lpr/                # LprService + models + normalizer + validator + result_storage
│       └── crossing/           # CrossingService + reglas + modelos
└── tests/
    ├── test_health.py
    ├── test_crossing_rules.py
    ├── test_camera_routes.py
    ├── test_snapshot_storage.py
    ├── test_plate_normalizer.py
    └── test_lpr_reads_routes.py
```

> **Nota sobre estructura legacy:** En el repositorio existen aún las carpetas
> `apps/` y `src/` del setup inicial. Quedan intactas por compatibilidad pero
> no se usan; el backend oficial vive bajo `app/`.

---

## Requisitos

- Python 3.11 o superior
- Webcam o stream RTSP (para pruebas reales de LPR)
- Token de Plate Recognizer (opcional para PoC; usar stub cuando no esté disponible)
- BioStar 2 accesible por red (opcional para PoC)

---

## Instalación

```powershell
# 1. Clonar (o ubicarse en) el repo
cd C:\intelca\Backend-HGAC

# 2. Crear y activar entorno virtual
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Copiar variables de entorno
copy .env.example .env
# editar .env con valores reales
```

---

## Variables de entorno

Todas viven en `.env`. Las principales:

| Variable | Descripción |
|---|---|
| `APP_ENV` | `development` / `staging` / `production` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `API_HOST`, `API_PORT` | Host/puerto del servidor |
| `PLATE_RECOGNIZER_API_TOKEN` | Token de Plate Recognizer |
| `CAMERA_PROVIDER` | `webcam` (defecto) o `rtsp` |
| `WEBCAM_INDEX` | Índice de la webcam (0 por defecto) |
| `RTSP_URL` | URL del stream RTSP cuando `CAMERA_PROVIDER=rtsp` |
| `BIOSTAR_HOST`, `BIOSTAR_PORT` | Host BioStar 2 |
| `BIOSTAR_USERNAME`, `BIOSTAR_PASSWORD` | Credenciales BioStar |
| `BIOSTAR_VERIFY_SSL` | `false` para certificados autofirmados |
| `RNTT_USE_STUB` | `true` para usar stub (PoC inicial sin Selenium) |
| `RNTT_PORTAL_URL` | URL del portal RNTT real |
| `CAMERA_REGISTRY_PATH` | Ruta del JSON de cámaras (def. `./config/cameras.json`); si no existe, se usa la webcam `CAM-P-01` |
| `CAMERA_P1_CARRIL_1_RTSP_URL`, `CAMERA_P1_CARRIL_2_RTSP_URL` | URLs RTSP reales referenciadas por `config/cameras.json` (`source_env`). Solo en tu `.env` local |
| `IGNITION_JSON_OUTPUT_DIR` | Carpeta donde escribir JSON de eventos para Ignition |
| `IGNITION_LPR_LATEST_PATH` | Archivo fijo del último LPR (def. `C:/Users/Public/hgac_lpr.json`); escritura atómica |

El archivo `.env.example` lista la plantilla completa.

---

## Cómo ejecutar

```powershell
# Activar venv si no está activo
.\.venv\Scripts\Activate.ps1

# Levantar el servidor en modo recarga
uvicorn app.main:app --reload
```

El backend queda en `http://localhost:8000`. La documentación interactiva
Swagger está en `http://localhost:8000/docs`.

---

## Endpoints mínimos

| Método | Ruta | Descripción |
|---|---|---|
| `GET`  | `/health` | Healthcheck |
| `POST` | `/lpr/read` | Captura un frame y detecta placa |
| `POST` | `/biostar/verify` | Verifica un usuario por `nombre_o_id` |
| `POST` | `/rntt/lookup` | Consulta una `placa` en RNTT |
| `POST` | `/crossing/evaluate` | Ejecuta el flujo completo y decide |

### Cámara (consumo por Ignition)

Ignition **no** se conecta a la cámara: consume estos endpoints REST, que
abstraen la fuente física tras un `camera_id` lógico (hoy `CAM-P-01`, webcam
USB; preparado para migrar a RTSP/ONVIF sin cambiar el contrato).

> **Registro de cámaras (`config/cameras.json`).** El catálogo de cámaras se
> carga desde un JSON externo (`CAMERA_REGISTRY_PATH`). Parte de
> `config/cameras.example.json` (versionado) → cópialo a `config/cameras.json`
> (**no** se versiona). Las URLs RTSP **nunca** van en el JSON: cada cámara
> referencia una variable de entorno con `source_env` (p.ej.
> `CAMERA_P1_CARRIL_1_RTSP_URL`), que el backend resuelve desde tu `.env`. Si el
> archivo no existe, el registro cae a la webcam por defecto `CAM-P-01`. La API
> **nunca** devuelve credenciales: el campo `source` del `/status` usa
> `safe_source`, que elimina usuario/clave de las URLs RTSP. Cada cámara puede
> declarar además un `lpr_roi` (región de interés) para uso futuro del LPR.

| Método | Ruta | Descripción |
|---|---|---|
| `GET`  | `/api/v1/cameras/CAM-P-01/status` | Estado de la cámara (online, resolución) |
| `GET`  | `/api/v1/cameras/CAM-P-01/snapshot.jpg` | Frame puntual como `image/jpeg` **en memoria** — diagnóstico/fallback. No persiste evidencia ni escribe temporales |
| `GET`  | `/api/v1/cameras/CAM-P-01/stream.mjpg` | **Live preview** MJPEG (`multipart/x-mixed-replace`). Mantiene la cámara abierta mientras dure el stream (no abre/cierra por frame). No persiste evidencia |
| `POST` | `/api/v1/cameras/CAM-P-01/snapshots` | Captura y **persiste** evidencia en `evidence/snapshots/`; devuelve metadata (`filename`, `path`, `url`, `size_bytes`, ...). Botón "Capturar snapshot" |

> **Tres usos, una cámara abstraída tras `CAM-P-01`:**
> - `/snapshot.jpg` = frame puntual / diagnóstico (efímero, en memoria).
> - `/stream.mjpg` = live preview MJPEG (flujo continuo; usar en lugar de
>   `now(250)`/`now(1000)`, que no escalan porque reabren la cámara por request).
> - `/snapshots` = evidencia persistida (escribe en disco; se sirve luego por
>   URL pública bajo `/evidence/snapshots/`).
>
> El stream usa un `CameraStreamManager` que abre la cámara **una sola vez** por
> `camera_id`, comparte el último frame entre clientes y la libera cuando el
> último se desconecta. Mientras hay stream activo, `snapshot.jpg`/`snapshots`
> reutilizan ese frame en vivo en vez de abrir el dispositivo en paralelo.
>
> FPS y calidad del stream se configuran con `CAMERA_STREAM_FPS` (defecto 10),
> `CAMERA_STREAM_JPEG_QUALITY`, `CAMERA_STREAM_WIDTH`, `CAMERA_STREAM_HEIGHT`.

**Ignition Perspective** — binding de `Image.props.source` para el botón
"Reproducir" (sin polling `now()`):

```
if(
    {view.custom.isPlaying},
    concat({view.custom.backendUrl}, "/api/v1/cameras/", {view.custom.cameraId}, "/stream.mjpg"),
    {view.custom.lastEvidenceUrl}
)
```

### LPR (lectura de placa)

Módulo independiente de `camera`: toma **un frame** de `CameraService` (reutiliza
el último frame del stream si está activo; nunca abre la cámara directamente),
detecta/lee la placa, normaliza, guarda evidencia LPR y devuelve JSON
estructurado. No decide acceso (eso será el futuro Decision Engine).

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/v1/lpr/reads` | Solicita una lectura de placa sobre un frame de la cámara indicada |

Diferencia Camera vs. LPR:

- **Camera `/snapshots`** → evidencia visual capturada manualmente (`evidence/snapshots/`).
- **Camera `/stream.mjpg`** → preview en vivo.
- **LPR `/reads`** → análisis de placa sobre un frame; guarda su propia evidencia
  en `evidence/lpr/frames/` (frame analizado) y `evidence/lpr/crops/` (recorte de
  placa, solo si hay detección). **Nunca** escribe en `evidence/snapshots/`.

Estados de respuesta: `PLATE_DETECTED`, `LOW_CONFIDENCE`, `FORMAT_MISMATCH`,
`AMBIGUOUS_READ`, `NO_PLATE_DETECTED`, `ERROR`. Cámara inexistente → 404; cámara
sin frame → 503.

**Aceptación estricta:** una lectura es `PLATE_DETECTED` solo si la confianza
supera `LPR_READ_MIN_CONFIDENCE` **y** la placa normalizada cumple alguno de los
formatos configurados. Si hay texto pero falla el formato → `FORMAT_MISMATCH`; si
falla la confianza → `LOW_CONFIDENCE`. En ambos casos `plate`/`plate_normalized`
quedan en `null` (no se acepta como placa) y el candidato se expone solo en los
campos de depuración; **no** se infieren ni autocompletan caracteres faltantes.

**Formatos configurables** (catálogo restrictivo, vía `LPR_PLATE_FORMAT_NAME`,
CSV de nombres; por defecto se aceptan ambos):

| Nombre | Regex | Ejemplo |
|---|---|---|
| `LETTER_6_DIGITS` | `^[A-Z][0-9]{6}$` | `L460432` |
| `TWO_LETTERS_5_DIGITS` | `^[A-Z]{2}[0-9]{5}$` | `OF00105` |

**Catálogo de placas dominicanas (DGII).** Con
`LPR_ENABLE_DOMINICAN_PLATE_CATALOG=true` (por defecto), la validación de formato
y la clasificación pasan a `app/modules/lpr/domain/` (no a regex sueltas en el
servicio ni al motor OCR). El catálogo es **referencia operativa para la PoC, no
fuente legal**: no autocorrige placas ni reemplaza la validación futura contra
RNTT/Navis/base autorizada. La respuesta añade `plate_type`, `vehicle_type` y
`format_pattern`, y cada entrada de `candidate_scores` se enriquece con
`format_valid`, `plate_type`, `vehicle_type`, `pattern_priority` y
`rejection_reason`. Patrones soportados:

| Patrón | Tipo | Patrón | Tipo |
|---|---|---|---|
| `A` + 6 díg. | Automóvil privado | `J` + 6 díg. | Montacargas |
| `G` + 6 díg. | Jeepeta | `PP` + 6 díg. | Provisional electrónica |
| `L` + 6 díg. | Carga | `EX` + 5 díg. | Exonerada |
| `F` + 6 díg. | Remolque | `DD` + 5 díg. | Dealer |
| `U` + 6 díg. | Máquina pesada | `OF/OP/OE/OM` + 5 díg. | Oficial |
| letra + 7 díg. | Motocicleta | | |

**Lecturas ambiguas (`AMBIGUOUS_READ`).** Si dos candidatos válidos comparten
prefijo y longitud, difieren en un solo carácter
(`LPR_AMBIGUOUS_CANDIDATE_DISTANCE`) y sus scores están más cerca que
`LPR_AMBIGUOUS_MIN_SCORE_DELTA`, la lectura **no se acepta automáticamente**
(`rejection_reason="ambiguous_digit_conflict"`, `plate=null`). Un caso como
`G237627` vs `G737627` debe resolverse por consenso multi-frame, más intentos
OCR, un score significativamente superior o match contra base autorizada —
**nunca** sustituyendo un carácter a mano. `LPR_REQUIRE_MULTIFRAME_CONFIRMATION`
queda preparado para exigir confirmación multi-frame (aún no altera la decisión).

**Motor (prioriza el serial, descarta el encabezado):** tras recortar la placa
(con padding) el OCR se enfoca en sub-ROIs del **serial** (zona inferior/central),
ignorando el ~32% superior donde va `REP. DOMINICANA`. Un candidato con **menos
de `LPR_MIN_SERIAL_DIGITS` (3) dígitos nunca puede ganar** (descarta `DOMIN`,
`REP`, `REPUBLICA`...). El scoring combina confianza, dígitos, longitud, mezcla
alfanumérica, formato y geometría (posición vertical, tamaño, ancho, centrado),
de modo que un serial gana aunque tenga menos confianza que el encabezado.

**Modos de rendimiento** (`LPR_MODE`, por defecto `balanced`) — antes una lectura
tardaba ~39 s; los modos acotan regiones/ROIs/variantes con *early stop*:

- `fast` — 1 región, ROI del serial, 2 variantes; sin frame completo. El más rápido.
- `balanced` — 1 región, 2 ROIs del serial, 2 variantes; sin frame completo.
- `exhaustive` — varias regiones, todas las ROIs/variantes + frame completo
  como último recurso. Solo para depuración (lento).

El OCR de frame completo es **último recurso** (solo `exhaustive`, y solo si las
ROIs no dieron nada).

Campos de depuración en la respuesta: `candidate_count`, `ocr_attempt_count`,
`best_raw_text`, `best_normalized_text`, `expected_format`, `format_valid`,
`rejection_reason`, `preprocessing_variant`, `crop_saved`, `selected_roi`,
`digit_count`, `alpha_count`, `candidate_rejections`, `candidate_scores`.

```bash
curl -X POST http://localhost:8000/api/v1/lpr/reads ^
     -H "Content-Type: application/json" ^
     -d "{\"camera_id\": \"CAM-P-01\", \"event_id\": \"LPR-MANUAL-001\", \"requested_by\": \"operator\"}"
```

Config (ver `.env.example`): `LPR_ENABLED`, `LPR_ENGINE` (`opencv_easyocr_poc`),
`LPR_READ_MIN_CONFIDENCE` (0-100, **separado** del legacy `LPR_MIN_CONFIDENCE`),
`LPR_MAX_PROCESSING_MS`, `LPR_EVIDENCE_BASE_PATH`. El motor EasyOCR se carga de
forma perezosa (no pesa en el arranque) y es reemplazable vía el contrato
`LprEngine`. Precisión inicial de PoC.

> El endpoint legacy `POST /lpr/read` (motor acoplado a `app/integrations/lpr`)
> se conserva intacto; el módulo formal nuevo vive bajo `/api/v1/lpr`.

**Publicación a Ignition (`hgac_lpr.json`).** Cada lectura de `/api/v1/lpr/reads`
se publica además en el archivo "latest" que el gateway de Ignition lee en bucle
(`IGNITION_LPR_LATEST_PATH`, def. `C:/Users/Public/hgac_lpr.json`). La escritura
es **atómica** (archivo temporal + `replace`) para no chocar con Ignition leyendo
el archivo. El contrato conserva las claves esperadas (`trigger`, `status`,
`plate`, `plate_normalized`, `confidence`, `camera_id/name/ip`, `frame_path/url`,
`crop_path/url`, `event_id`, `engine`, `plate_matched`, campos de consenso y
`raw_result`). En lecturas no aceptadas `plate` queda vacío y `plate_matched=false`
(no se infieren caracteres). Si el archivo está bloqueado, el fallo se registra
**sin** romper la respuesta HTTP.

### Ejemplos rápidos

```bash
# Healthcheck
curl http://localhost:8000/health

# Verificar usuario en BioStar
curl -X POST http://localhost:8000/biostar/verify ^
     -H "Content-Type: application/json" ^
     -d "{\"nombre_o_id\": \"42\"}"

# Consultar placa en RNTT (stub)
curl -X POST http://localhost:8000/rntt/lookup ^
     -H "Content-Type: application/json" ^
     -d "{\"placa\": \"A123456\"}"

# Evaluar cruce
curl -X POST http://localhost:8000/crossing/evaluate ^
     -H "Content-Type: application/json" ^
     -d "{\"gate_id\": \"GATE_01\", \"lane_id\": \"LANE_01\", \"driver_identifier\": \"42\"}"
```

---

## Tests

```powershell
pytest -q
```

Cubre el endpoint `/health` y todas las ramas de las reglas de cruce.

---

## Monitores independientes (`scripts/`)

Procesos opcionales que corren **fuera** del backend (no son dependencias del
arranque) y publican su salida para Ignition:

| Script | Qué hace |
|---|---|
| `scripts/lpr/simplelpr_rtsp_monitor.py` | Monitor LPR continuo con **SimpleLPR + RTSP**. Publica cada placa/rótulo en `hgac_lpr.json` (mismo contrato que el endpoint formal). Dependencia **opcional/comercial**: instálala con `pip install -r scripts/lpr/requirements-simplelpr.txt` + el SDK SimpleLPR. No exige nada al backend |
| `scripts/monitor_biostar_local.py` | Monitor del lector BioStar local; publica el último evento en `BIOSTAR_LOCAL_OUTPUT_PATH` (lo expone `GET /biostar/events/latest`) |
| `scripts/poc_supervisor.py` | Lanza y reinicia en una sola consola los tres procesos del PoC (backend + LPR + BioStar). Toda credencial/URL viene de `.env`; el monitor BioStar solo arranca por defecto si `BIOSTAR_LOCAL_PASSWORD` está definida |

```powershell
# Monitor SimpleLPR (configura SIMPLELPR_RTSP_URL en .env primero)
.\.venv\Scripts\python.exe .\scripts\lpr\simplelpr_rtsp_monitor.py

# Supervisor del PoC completo
.\.venv\Scripts\python.exe .\scripts\poc_supervisor.py
```

> SimpleLPR es un proveedor **alternativo/temporal**: el motor propio
> OpenCV+EasyOCR (`/api/v1/lpr/reads`) permanece como respaldo y no depende de él.
> Variables del monitor en `scripts/lpr/.env.example` (cópialas a tu `.env` raíz).

---

## Integraciones del handoff (RNTT / BioStar / Navis / Wialon)

Estas integraciones provienen de scripts de prueba ya validados por el equipo
contra los sistemas reales del puerto. Se portaron a módulos backend limpios
(`client` + `service` + `models` + `factory`, igual que el resto) y se exponen
bajo `/api/v1/integrations`. **Cada "monitor continuo" de los scripts originales
se convirtió en un servicio consultable (una llamada = un tick); el monitoreo
periódico queda como paso siguiente (no hay background jobs en el backend hoy).**

> ⚠️ **LPR del zip excluido a propósito.** El handoff incluía un LPR basado en
> Plate Recognizer (API de paga) y `test_camera_lpr.py`. **No se integró**: el
> proyecto ya tiene su propio LPR (`app/integrations/lpr` + `app/modules/lpr`,
> `POST /api/v1/lpr/reads`), que queda **intacto**. No se añadió
> `PLATE_RECOGNIZER_TOKEN` ni lógica de Plate Recognizer del zip.

### Endpoints nuevos

| Método | Ruta | Descripción |
|---|---|---|
| `GET`  | `/api/v1/integrations/health` | Qué integraciones están configuradas (sin exponer secretos) |
| `POST` | `/api/v1/integrations/rntt/query` | Consulta RNTT ASMX: chofer (`rntt`/`licencia`/`cedula`) o camión (`placa`/`rotulo`/`chasis`/`rfid`) |
| `POST` | `/api/v1/integrations/rntt/combined-query` | Consulta combinada chofer↔camión por Rótulo |
| `POST` | `/api/v1/integrations/navis/query` | Navis: `truck-info` y/o `driver-info` consolidados |
| `GET`  | `/api/v1/integrations/biostar/devices` | Lista dispositivos BioStar (resuelve IP del nombre) |
| `POST` | `/api/v1/integrations/biostar/events/recent` | Eventos recientes (opcional: por dispositivo / solo validación) |
| `POST` | `/api/v1/integrations/biostar/validate-event` | Valida un evento de acceso → PASA/NO PASA |
| `GET`  | `/api/v1/integrations/wialon/units` | Resumen de todas las unidades GPS + clasificación de geocercas |
| `GET`  | `/api/v1/integrations/wialon/unit/{unit_id_or_name}` | Una unidad por ID interno, unique ID/IMEI o nombre |

Errores: timeout → `504`; auth/upstream → `502`; recurso no encontrado
(dispositivo/unidad) → `404`; integración **no configurada** → `503`.

### Variables de entorno

| Variable | Descripción |
|---|---|
| `RNTT_BASE_URL`, `RNTT_USERNAME`, `RNTT_PASSWORD` | API ASMX RNTT |
| `RNTT_AUTH_MODE` | `header` (confirmado en producción) o `hmac` |
| `RNTT_ENABLE_DIAGNOSTIC_FALLBACKS` | DEBUG: intentos extra del script original (incluye no-auth). Dejar `false` |
| `BIOSTAR_HOST`, `BIOSTAR_PORT`, `BIOSTAR_USERNAME`, `BIOSTAR_PASSWORD` | BioStar 2 remoto |
| `BIOSTAR_VERIFY_SSL` | Verificación SSL **explícita** (`false` para autofirmados) |
| `BIOSTAR_DISPLAY_TIMEZONE` | Zona para mostrar eventos (def. `America/Santo_Domingo`) |
| `BIOSTAR_USERS_CACHE_TTL_SECONDS`, `BIOSTAR_EVENTS_HOURS_BACK` | Caché de padrón / ventana de eventos |
| `BIOSTAR_LOCAL_HOST/PORT/SCHEME/USER/PASSWORD` | Perfil BioStar local (lector facial) |
| `NAVIS_API_BASE`, `NAVIS_TOKEN_URL`, `NAVIS_TOKEN_PATH` | Endpoints Navis (OAuth) |
| `NAVIS_GRANT_TYPE`, `NAVIS_CLIENT_ID`, `NAVIS_CLIENT_SECRET`, `NAVIS_USERNAME`, `NAVIS_PASSWORD`, `NAVIS_SCOPE` | Credenciales OAuth password grant |
| `WIALON_TOKEN`, `WIALON_HOST` | Token y host Wialon (nube o local de HIT) |
| `WIALON_ONLINE_SECONDS` | Edad máx. del GPS para marcar "online" |
| `WIALON_TERMINAL_GEOFENCE_NAMES`, `WIALON_GATE_ZONE_KEYWORDS` | Geocercas (CSV) |

Plantilla completa en `.env.example`. Todas las credenciales vienen de
configuración: **nada hardcodeado y nunca se loguean tokens/passwords**.

### Cómo probar (con mocks, sin servicios reales)

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # incluye tzdata (zoneinfo en Windows)
pytest -q
```

Las pruebas (`tests/test_rntt_asmx.py`, `test_navis.py`, `test_wialon.py`,
`test_biostar_events.py`) inyectan clientes/sesiones falsas en los servicios —
**no hacen llamadas externas reales**. `GET /api/v1/integrations/health` responde
sin credenciales; el resto devuelve `503` mientras no se configuren.

### Mapeo a tags de Ignition (contrato de datos)

Los scripts `ignition_scripts/HGAC_0x` se usaron **solo como referencia del shape
esperado**. Las respuestas REST replican las claves que esos scripts leen, para
que el ingest de Ignition sea de mapeo mínimo (no se modifica el LPR ni su
contrato; no se añadió writer de archivos ni base de datos):

| Sistema | Respuesta REST → tags Ignition |
|---|---|
| BioStar | `validate-event` → `permitir_paso`, `decision_sugerida`, `estado`, `motivo`, `credentials.{has_card,has_fingerprint,has_face,event_method}`, `event_time`, `event_type`, `event_type_code`, `user_id`, `nombre`, `device.{id,name,ip}` |
| RNTT | `combined-query` → `{source, queried_at, driver, truck, related_queries}` |
| Navis | `query` → `{source, timestamp, success, status, truck, driver, results}` |
| Wialon | `units` → `{unidades[], selected_unit{...}}` con `id/nombre/lat/lon/velocidad/online/inside_terminal/inside_gate_zone/...` |

### Pendiente para producción

- Monitoreo continuo (background tasks/worker) en vez de polling por endpoint.
- Confirmar definitivamente la autenticación RNTT y obtener el **catálogo oficial**
  de estados (los actuales son *observados/inferidos*, no oficiales).
- Persistencia/auditoría de decisiones (hoy el backend no persiste).
- Sesión BioStar/SID Wialon de larga duración y manejo de carga del padrón grande.

## Decisiones de diseño

- **Configuración centralizada en `app/core/config.py`** — un único `Settings`
  con `pydantic-settings`. Nadie más toca `os.environ`.
- **Excepciones del dominio en `app/core/errors.py`** — cada integración
  lanza errores tipados que la capa HTTP mapea a códigos coherentes (502 para
  fallos de upstream, 504 para timeouts, 422 para datos inválidos, etc.).
- **Cada integración tiene `client` + `service` + `models` + `factory`** —
  el cliente solo habla con el sistema externo; el servicio aplica lógica de
  negocio; los modelos exponen tipos limpios; el factory inyecta dependencias.
- **`crossing_rules.evaluate_crossing` es una función pura** — sin IO, sin
  servicios, totalmente testeable sin mocks.
- **Selenium aislado** — la integración RNTT define una interfaz `RnttClient`
  con dos implementaciones (`StubRnttClient` y `SeleniumRnttClient`), así el
  resto del backend nunca importa Selenium directamente.
- **Ignition como puente JSON** — `IgnitionJsonWriter` deja archivos en una
  carpeta configurable; la integración REST nativa queda lista para activarse
  cuando Ignition pueda consumir el API.

---

## Roadmap PoC

- [x] Portar las integraciones del handoff (RNTT ASMX, BioStar eventos, Navis, Wialon) a `/api/v1/integrations`.
- [ ] Monitoreo continuo (background worker) para BioStar/Wialon en vez de polling por endpoint.
- [ ] Portar el script Selenium real al `SeleniumRnttClient`.
- [ ] Integrar Mobotix vía `RtspCameraProvider`.
- [ ] Cambiar puente JSON por consumo REST nativo desde Ignition.
- [ ] Persistir decisiones en SQLite (`data/hgac_poc.db`) para auditoría.
