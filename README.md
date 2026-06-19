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
| `IGNITION_JSON_OUTPUT_DIR` | Carpeta donde escribir JSON para Ignition |

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
`NO_PLATE_DETECTED`, `ERROR`. Cámara inexistente → 404; cámara sin frame → 503.

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

- [ ] Portar el script Selenium real al `SeleniumRnttClient`.
- [ ] Integrar Mobotix vía `RtspCameraProvider`.
- [ ] Cambiar puente JSON por consumo REST nativo desde Ignition.
- [ ] Persistir decisiones en SQLite (`data/hgac_poc.db`) para auditoría.
