# Integración de SimpleLPR (motor de placa) + fallback + rótulo

Esta guía explica cómo activar **SimpleLPR** como motor de lectura de placas del
endpoint `POST /api/v1/lpr/reads`, cómo funciona el **fallback** a OpenCV/EasyOCR,
y cómo se lee el **rótulo** de camión. SimpleLPR es un SDK **comercial y opcional**:
si no está instalado, no hay licencia o falla, el backend **no se rompe** — cae al
motor OpenCV/EasyOCR o devuelve un error controlado (503), según configuración.

> RTSP/credenciales nunca van en código ni en el repo. Las URLs RTSP se resuelven
> por variable de entorno (`source_env` en `config/cameras.json`).

---

## 1. Arquitectura del pipeline

```
Ignition Perspective → Cloudflare Tunnel → Backend-HGAC → RTSP (VPN) → CameraService
   POST /api/v1/lpr/reads
      ├─ captura frame completo  → evidencia (frame)
      ├─ recorta lpr_roi         → motor de PLACA (SimpleLPR o OpenCV) → engine_attempts
      ├─ recorta rotulo_roi      → motor de RÓTULO (OpenCV/EasyOCR)
      ├─ valida placa (catálogo dominicano) y rótulo (validador propio)
      ├─ evidencia debug: ROI placa, ROI rótulo, overlay con recuadros
      └─ respuesta JSON (placa + rótulo + candidatos + URLs)  → Ignition tags
```

- **SimpleLPR** hace detección/OCR de placa. La **autoridad de formato** sigue
  siendo el backend (catálogo dominicano DGII): SimpleLPR no tiene plantilla de
  República Dominicana, así que no se rechaza por país.
- El **rótulo** (identificador corto pintado, p.ej. `E204`) usa el motor
  OpenCV/EasyOCR sobre `rotulo_roi`, con un validador distinto al de placa.

---

## 2. Instalación de SimpleLPR

SimpleLPR se distribuye como wheel (PyPI). Es opcional: instálalo solo si vas a
usar `LPR_ENGINE=simplelpr` o `LPR_ENGINE=auto`.

```bash
pip install SimpleLPR
# Verifica el import real del paquete:
python -c "import simplelpr; print(simplelpr)"
```

- Referencias oficiales:
  - PyPI: <https://pypi.org/project/SimpleLPR/>
  - Samples: <https://github.com/xgirones/SimpleLPR-samples>
  - Quickstart Python: <https://www.warelogic.com/doc/simplelpr_python_quickstart_guide.htm>
  - API reference: <https://www.warelogic.com/doc/simplelpr_python_api_reference.htm>
- Si SimpleLPR vive en un Python distinto al `.venv` del backend, instálalo en el
  **mismo** intérprete que ejecuta el backend (o ajusta el entorno del servicio).
- El backend NO importa `simplelpr` salvo que el motor activo lo requiera (import
  perezoso). Sin el paquete, los tests y el endpoint siguen funcionando.

> **Licencia**: SimpleLPR es comercial. Sin licencia funciona en **modo
> evaluación** (limitado en tiempo, según el SDK). Para producción se requiere
> una licencia válida. La ruta a la licencia se pasa por variable de entorno
> (`SIMPLE_LPR_PRODUCT_KEY_PATH` / `SIMPLELPR_LICENSE_PATH`), **nunca** en código.

---

## 3. Activación por `.env`

Copia `.env.example` a `.env` y ajusta (sin secretos en el repo):

```dotenv
# Motor de placa: opencv_easyocr (default) | simplelpr | auto
LPR_ENGINE=auto
# Respaldo cuando el primario es SimpleLPR/auto y no detecta o falla:
LPR_FALLBACK_ENGINE=opencv_easyocr

# SimpleLPR (opcional). Acepta también los alias del spec:
SIMPLE_LPR_ENABLED=true                 # == SIMPLELPR_ENABLED
SIMPLE_LPR_COUNTRIES=19,74,96           # == SIMPLELPR_COUNTRY_CODES (latinos vecinos)
SIMPLE_LPR_PRODUCT_KEY_PATH=            # == SIMPLELPR_LICENSE_PATH (vacío = evaluación)
SIMPLE_LPR_MIN_CONFIDENCE=55            # 0-100  (NO uses SIMPLELPR_MIN_CONFIDENCE: ese
                                        # nombre lo usa el monitor continuo, escala 0-1)

# Evidencia de depuración (calibración de ROI desde Ignition):
LPR_SAVE_DEBUG_EVIDENCE=true

# Rótulo de camión:
LPR_ROTULO_ENABLED=true
LPR_ROTULO_READ_MIN_CONFIDENCE=60
```

---

## 4. Modos de motor (`LPR_ENGINE`)

| Valor | Alias | Comportamiento |
|-------|-------|----------------|
| `opencv_easyocr_poc` | `opencv_easyocr`, `opencv` | Motor propio OpenCV+EasyOCR. Sin fallback. |
| `simplelpr_rd_poc` | `simplelpr` | SimpleLPR. Si no se puede construir y hay `LPR_FALLBACK_ENGINE`, degrada al fallback sin romper. Sin fallback → 503 controlado. |
| `auto` | — | Intenta SimpleLPR como primario y OpenCV como fallback. Si SimpleLPR no está disponible, usa OpenCV como primario. **Nunca rompe.** |

En `auto`/fallback, por cada lectura:
1. Se intenta el **primario**. Si **detecta**, se usa y el resto queda `NOT_USED`.
2. Si el primario **falla** (excepción) o **no detecta**, se intenta el **fallback**.
3. Cada intento queda en `engine_attempts` con su estado:
   `OK | NO_DETECTION | ERROR | NOT_USED | UNAVAILABLE`.

El campo legacy `engine` refleja el motor usado; si se usó el fallback, una
etiqueta combinada (p.ej. `simplelpr_rd_poc+opencv_easyocr_poc_fallback`).

---

## 5. Calibración de ROI y evidencia visual

El problema más común de "no lee placas" es un **ROI mal encuadrado** (la placa
queda fuera o cortada). El backend guarda evidencia para calibrar desde Ignition:

- `debug_frame_url` — frame completo analizado.
- `plate_roi_url` — recorte del ROI de placa que recibió el OCR.
- `rotulo_roi_url` — recorte del ROI de rótulo.
- `roi_overlay_url` — frame completo con los recuadros dibujados
  (verde=placa, azul=rótulo, rojo=bbox del motor) y etiquetas.

Define los ROI en `config/cameras.json` (ver `config/cameras.example.json`):

```jsonc
{
  "camera_id": "CAM-HIT-LPR-01",
  "source_type": "rtsp",
  "source_env": "CAMERA_HIT_LPR_01_RTSP_URL",
  "lpr_roi":    { "x": 350, "y": 360, "width": 650, "height": 350 },
  "rotulo_roi": { "x": 0,   "y": 100, "width": 800, "height": 700 }
}
```

> ancho/alto `0` = "sin ROI" (procesa el frame completo para esa lectura). El ROI
> se acota automáticamente a los bordes del frame; si queda fuera, se cae al frame
> completo (no se entrega imagen vacía al OCR).

**Flujo de calibración**: dispara una lectura, abre `roi_overlay_url` y
`plate_roi_url`, verifica que la placa caiga completa dentro del recuadro verde y
ajusta `lpr_roi` hasta encuadrarla con algo de margen.

---

## 6. Rótulo de camión

- Usa `rotulo_roi` (independiente de `lpr_roi`) y el motor OpenCV/EasyOCR.
- Validador propio: `LETTER_3_DIGITS` (`^[A-Z][0-9]{3}$`, p.ej. `E204`) y, laxo,
  `LETTERS_2_4_DIGITS`. **No** se valida como placa dominicana ni viceversa.
- Respuesta: `rotulo`, `rotulo_normalized`, `rotulo_confidence`, `rotulo_status`
  (`ROTULO_DETECTED | NO_ROTULO_DETECTED | LOW_CONFIDENCE | FORMAT_MISMATCH |
  NOT_CONFIGURED | ERROR`), `rotulo_engine`, `rotulo_candidates`, `rotulo_roi`,
  `rotulo_crop_url`.
- Si la cámara no define `rotulo_roi`, el rótulo queda `NOT_CONFIGURED` (no se
  ejecuta OCR de rótulo).

---

## 7. Validación dominicana (independiente del motor)

SimpleLPR/EasyOCR solo hacen detección/OCR. El backend aplica:
`PlateNormalizer` → catálogo dominicano (`DominicanPlatePatternCatalog`) /
`PlateValidator`. Un candidato se expone en `plate_candidates` **aunque sea
rechazado** (para depurar qué leyó el motor), pero solo se acepta como `plate` si
cumple confianza **y** formato dominicano.

---

## 7b. Ráfaga multiframe + consenso (placas en movimiento)

Los vehículos no siempre se detienen. En vez de procesar un solo frame (que puede
salir borroso por el movimiento), cada lectura captura una **ráfaga** y decide por
**consenso**:

1. Captura `LPR_BURST_FRAME_COUNT` frames con `LPR_BURST_INTERVAL_MS` de intervalo
   (12 × 120 ms ≈ 1.4 s de ventana), en una sola sesión RTSP.
2. Puntúa la calidad del **ROI de placa** de cada frame: nitidez (varianza de
   Laplaciano) y brillo. Descarta los borrosos/quemados
   (`LPR_MIN_FRAME_SHARPNESS`, `LPR_MIN/MAX_FRAME_BRIGHTNESS`).
3. Procesa con el/los motor(es) solo los **mejores** `LPR_BURST_TOP_FRAMES`.
4. Agrupa los candidatos por placa normalizada y **vota**:
   `score = max_conf*0.55 + avg_conf*0.25 + min(votos,3)*10 + calidad*20`.
5. Acepta si `format_valid` y (votos ≥ `LPR_CONSENSUS_MIN_VOTES` y
   `max_conf ≥ LPR_READ_MIN_CONFIDENCE`) **o** (`max_conf ≥
   LPR_SINGLE_FRAME_ACCEPT_CONFIDENCE`). Si no, `LOW_CONFIDENCE` /
   `FORMAT_MISMATCH` / `NO_PLATE_DETECTED`; si ningún frame fue utilizable,
   `BLURRY_FRAME`.

`LPR_BURST_FRAME_COUNT=1` desactiva la ráfaga (comportamiento single-frame). La
respuesta añade `burst_frame_count`, `processed_frame_count`, `usable_frame_count`,
`best_frame_index`, `best_frame_sharpness/brightness`, `consensus_votes/total/ratio`
y `plate_candidates[].frame_index/sharpness/brightness/frame_quality_score`. La
evidencia se guarda SOLO del mejor frame (más los top frames si
`LPR_SAVE_BURST_FRAMES=true`).

## 8. Limitaciones conocidas

- **Licencia**: en producción SimpleLPR puede exigir licencia válida; el modo
  evaluación es temporal.
- **País RD**: SimpleLPR no trae plantilla de República Dominicana; se usan países
  latinos vecinos solo para habilitar OCR. La validación de formato es del backend.
- **Bounding box**: la integración v1 no extrae el polígono/bbox de placa de
  SimpleLPR (mejora futura); el overlay dibuja los ROI configurados.
- **Memoria**: si el motor de placa **y** el de rótulo son OpenCV/EasyOCR, se
  cargan dos lectores EasyOCR (carga perezosa: solo si una cámara con `rotulo_roi`
  dispara lectura).
- **Encuadre/iluminación/distancia**: el OCR no resuelve una placa fuera del ROI,
  muy pequeña, borrosa o mal iluminada. Para LPR confiable conviene cámara
  dedicada, buen ángulo y, si aplica, iluminación IR.
