"""Configuración centralizada del backend.

Todas las variables se cargan desde `.env` (o entorno real en producción).
Nunca debe haber credenciales o hosts hardcodeados fuera de este módulo.
"""

from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Carga `.env` en `os.environ` para que componentes que leen variables por nombre
# en tiempo de ejecución (p.ej. `CameraRegistry.from_json`, que resuelve las URLs
# RTSP desde `source_env`) las encuentren. `pydantic-settings` ya lee `.env` para
# poblar `Settings`, pero no exporta esas variables a `os.environ`; no sobreescribe
# variables ya presentes en el entorno (override=False por defecto).
load_dotenv()


class Settings(BaseSettings):
    # --- App ---
    app_name: str = "Backend HGAC PoC"
    app_env: str = "development"
    log_level: str = "INFO"

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Persistencia / Evidencia (compatibilidad con setup previo) ---
    database_url: str = "sqlite:///./data/hgac_poc.db"
    evidence_base_path: str = "./evidence/snapshots"
    evidence_public_base_url: str = "http://localhost:8000/evidence"

    # --- LPR ---
    # Opciones:
    # - local: OCR gratuito ejecutado dentro del backend.
    # - plate_recognizer: API externa comercial, conservada como fallback.
    lpr_provider: Literal["local", "plate_recognizer"] = "local"
    lpr_min_confidence: float = 0.50

    # --- LPR local gratuito ---
    local_lpr_ocr_engine: Literal["easyocr"] = "easyocr"
    local_lpr_gpu: bool = False
    local_lpr_region: str = "do"
    local_lpr_use_fixed_roi: bool = False
    local_lpr_roi_x: int = 0
    local_lpr_roi_y: int = 0
    local_lpr_roi_width: int = 0
    local_lpr_roi_height: int = 0
    local_lpr_min_text_length: int = 5
    local_lpr_max_text_length: int = 8

    # --- Plate Recognizer (LPR externo opcional) ---
    plate_recognizer_api_token: str = ""
    plate_recognizer_api_url: str = "https://api.platerecognizer.com/v1/plate-reader/"
    plate_recognizer_regions: str = "do"
    plate_recognizer_timeout_seconds: int = 10

    # --- LPR módulo (POST /api/v1/lpr/reads sobre un frame de cámara) ---
    # Nota: `lpr_read_min_confidence` está en escala 0-100 (la respuesta usa %),
    # distinta del legacy `lpr_min_confidence` (0-1) de /lpr/read; son settings
    # separados a propósito para no romper el endpoint antiguo.
    lpr_enabled: bool = True
    # Motor LPR activo. Valores soportados (alias resueltos en la factory):
    #   - opencv_easyocr_poc / opencv_easyocr : motor propio OpenCV + EasyOCR (default).
    #   - simplelpr_rd_poc / simplelpr        : motor SimpleLPR (dependencia opcional).
    #   - auto                                : intenta SimpleLPR y cae a OpenCV.
    # El valor se valida en la factory (`_build_lpr_engines`), no aquí, para dar
    # un error claro sin impedir el arranque por una validación de Settings.
    lpr_engine: str = "opencv_easyocr_poc"
    # Motor de respaldo cuando el primario es SimpleLPR/auto y no detecta o falla
    # de forma controlada. Vacío = sin fallback (un primario SimpleLPR ausente
    # devolvería 503 controlado). Default: OpenCV/EasyOCR.
    lpr_fallback_engine: str = "opencv_easyocr"
    # Escala 0-100; validado para evitar confundirlo con el legacy (0-1). Umbral de
    # confianza para aceptar una placa; con la ráfaga multiframe se baja respecto al
    # single-frame porque el consenso (varios votos) compensa una confianza menor.
    lpr_read_min_confidence: float = Field(default=55.0, ge=0, le=100)

    # --- LPR: ráfaga multiframe + consenso (placas en movimiento) ---
    # Se captura una ráfaga por lectura; se puntúa la calidad del ROI de cada frame,
    # se procesan los mejores y se vota por consenso. count/interval cubren ~1-2 s de
    # paso del vehículo. count=1 => comportamiento single-frame (compatibilidad).
    lpr_burst_frame_count: int = Field(default=12, ge=1, le=30)
    lpr_burst_interval_ms: int = Field(default=120, ge=0)
    lpr_burst_top_frames: int = Field(default=5, ge=1)
    # Umbrales de calidad del ROI de placa (Laplaciano y brillo 0-255).
    lpr_min_frame_sharpness: float = 80.0
    lpr_min_frame_brightness: float = 30.0
    lpr_max_frame_brightness: float = 235.0
    # Consenso: votos mínimos (frames distintos con la misma placa) y confianza para
    # aceptar una placa válida vista en un solo frame.
    lpr_consensus_min_votes: int = Field(default=2, ge=1)
    lpr_single_frame_accept_confidence: float = Field(default=75.0, ge=0, le=100)
    # Guardar los top frames de la ráfaga como evidencia (burst_frame_urls). Off por
    # defecto para no llenar evidence/ con archivos.
    lpr_save_burst_frames: bool = False
    lpr_save_debug_frames: bool = True
    lpr_max_processing_ms: int = 5000
    lpr_evidence_base_path: str = "./evidence/lpr"
    # Modo de rendimiento del motor: fast (rápido) | balanced | exhaustive (debug).
    lpr_mode: Literal["fast", "balanced", "exhaustive"] = "balanced"
    # Mínimo de dígitos para que un candidato pueda ser placa (descarta encabezados).
    lpr_min_serial_digits: int = 3
    # Padding del recorte de placa antes del OCR. Asimétrico: más margen a la
    # izquierda para no cortar la letra inicial de la placa.
    lpr_pad_left_ratio: float = 0.35
    lpr_pad_right_ratio: float = 0.15
    lpr_pad_y_ratio: float = 0.12
    # Formato(s) esperado(s) de placa: CSV de nombres del catálogo
    # (LETTER_6_DIGITS, TWO_LETTERS_5_DIGITS). Criterio DURO de aceptación: una
    # lectura solo es PLATE_DETECTED si cumple alguno; si no, FORMAT_MISMATCH.
    lpr_plate_format_name: str = "LETTER_6_DIGITS,TWO_LETTERS_5_DIGITS"
    # Override de regex SOLO para un nombre fuera del catálogo (vacío = catálogo).
    lpr_plate_format_regex: str = ""
    lpr_plate_expected_length: int = 7

    # --- LPR: catálogo de placas dominicanas (referencia operativa DGII) ---
    # Catálogo operativo para la PoC; NO sustituye validación futura contra
    # RNTT/Navis/base autorizada y NO autocorrige placas.
    lpr_enable_dominican_plate_catalog: bool = True
    # Distancia de caracteres para considerar dos candidatos "casi iguales" (1 = un carácter).
    lpr_ambiguous_candidate_distance: int = 1
    # Si la diferencia de score entre dos candidatos válidos es MENOR a esto, es ambiguo.
    lpr_ambiguous_min_score_delta: float = 15.0
    # Preparado para exigir confirmación multi-frame (aún no implementado).
    lpr_require_multiframe_confirmation: bool = False

    # --- LPR: evidencia de depuración (recortes de ROI + overlay) ---
    # Guarda recorte del ROI de placa, del ROI de rótulo y un overlay del frame con
    # los recuadros, para CALIBRAR el encuadre desde Ignition. Solo se escribe si la
    # cámara tiene algún ROI configurado; nunca rompe la respuesta.
    lpr_save_debug_evidence: bool = True
    lpr_evidence_jpeg_quality: int = Field(default=90, ge=40, le=100)

    # --- LPR: lectura de RÓTULO de camión (identificador corto pintado) ---
    # Independiente de la placa: usa `rotulo_roi` de la cámara, un validador propio
    # (LETTER_3_DIGITS, p.ej. E204) y el motor OpenCV/EasyOCR (SimpleLPR es para
    # placas). Si la cámara no define `rotulo_roi`, no se intenta leer rótulo.
    lpr_rotulo_enabled: bool = True
    # CSV de formatos de rótulo del catálogo (vacío = LETTER_3_DIGITS + LETTERS_2_4_DIGITS).
    lpr_rotulo_format_name: str = ""
    lpr_rotulo_read_min_confidence: float = Field(default=60.0, ge=0, le=100)
    lpr_rotulo_min_text_length: int = 3
    lpr_rotulo_max_text_length: int = 6
    lpr_rotulo_min_serial_digits: int = 2

    # --- LPR: motor alternativo SimpleLPR (RD por países vecinos) ---
    # SimpleLPR no tiene plantilla de República Dominicana; se activan países de
    # alfabeto latino vecinos SOLO para habilitar OCR alfanumérico. La AUTORIDAD
    # de formato sigue siendo el catálogo dominicano del backend, no estos países.
    # SimpleLPR es dependencia OPCIONAL: solo se importa si lpr_engine es
    # `simplelpr_rd_poc` (import perezoso en el engine/factory).
    # Acepta también los nombres de entorno del spec (SIMPLELPR_*) además de los
    # SIMPLE_LPR_* históricos, vía AliasChoices.
    simple_lpr_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("simple_lpr_enabled", "simplelpr_enabled"),
    )
    # CSV de países a activar por índice o nombre (19=Colombia, 74=Puerto Rico, 96=Venezuela).
    simple_lpr_countries: str = Field(
        default="19,74,96",
        validation_alias=AliasChoices("simple_lpr_countries", "simplelpr_country_codes"),
    )
    # Ruta opcional al archivo de licencia (.xml). Vacío = modo evaluación (60 días).
    simple_lpr_product_key_path: str = Field(
        default="",
        validation_alias=AliasChoices(
            "simple_lpr_product_key_path", "simplelpr_license_path"
        ),
    )
    # Confianza mínima (escala 0-100) para que un match OCR de SimpleLPR se considere.
    # OJO: sin alias SIMPLELPR_MIN_CONFIDENCE a propósito: ese nombre ya lo usa el
    # monitor SimpleLPR continuo en escala 0-1; usar SIMPLE_LPR_MIN_CONFIDENCE aquí.
    simple_lpr_min_confidence: float = Field(default=55.0, ge=0, le=100)
    # GPU/CPU. cuda_device_id=-1 y use_gpu=false => CPU. max_concurrent_ops=0 => auto.
    simple_lpr_use_gpu: bool = False
    simple_lpr_cuda_device_id: int = -1
    simple_lpr_max_concurrent_ops: int = 0
    # Detección/recorte de región de placa por SimpleLPR (si el binding lo soporta).
    simple_lpr_plate_region_detection: bool = True
    simple_lpr_crop_to_plate_region: bool = False
    # Corrección OCR posicional (RD): nº máximo de sustituciones letra<->dígito antes
    # de penalizar fuerte la confianza, y penalización por sustitución (escala 0-100).
    # Evita aceptar como definitiva una placa que solo "cuadra" tras demasiados cambios.
    simple_lpr_max_ocr_substitutions: int = 2
    simple_lpr_substitution_penalty: float = 12.0
    # Tracker multi-frame de SimpleLPR. NO usado en la integración v1 (un frame por
    # request; el consenso vive en LprService). Reservado para una mejora posterior.
    simple_lpr_trigger_window_seconds: float = 3.0
    simple_lpr_max_idle_seconds: float = 2.0
    simple_lpr_min_trigger_frame_count: int = 3
    simple_lpr_thumbnail_width: int = 256
    simple_lpr_thumbnail_height: int = 128
    # Modo de fallback de país (documental): de momento solo `latin_neighbors`.
    simple_lpr_country_fallback_mode: str = "latin_neighbors"

    # --- Cámara ---
    camera_provider: Literal["webcam", "rtsp"] = "webcam"
    webcam_index: int = 0
    rtsp_url: str = ""
    camera_capture_timeout_seconds: int = 5
    # Registro de cámaras cargado desde JSON (URLs RTSP por `source_env`, nunca
    # versionadas). Si el archivo no existe, se usa la webcam por defecto CAM-P-01.
    camera_registry_path: str = "./config/cameras.json"

    # --- Cámara: live stream MJPEG (preview en Ignition) ---
    camera_stream_fps: int = 10
    camera_stream_jpeg_quality: int = 75
    camera_stream_width: int = 640
    camera_stream_height: int = 480
    # Margen para el PRIMER frame al iniciar el stream. En Windows/DirectShow la
    # apertura + warm-up de la webcam puede tardar varios segundos; debe ser
    # holgado o el primer cliente recibiría un 503 falso.
    camera_stream_open_timeout_seconds: int = 15

    # --- BioStar 2 ---
    biostar_host: str = ""
    biostar_port: int = 443
    biostar_username: str = ""
    biostar_password: str = ""
    biostar_verify_ssl: bool = False
    biostar_timeout_seconds: int = 10
    # Zona horaria para mostrar los eventos de BioStar (UTC en el API → local).
    biostar_display_timezone: str = "America/Santo_Domingo"
    # Caché en memoria del padrón de usuarios (estado activo + credenciales).
    biostar_users_cache_ttl_seconds: int = 60
    # Ventana hacia atrás al consultar eventos recientes.
    biostar_events_hours_back: int = 24
    # Perfil BioStar local (lector facial conectado a esta PC). Opcional.
    biostar_local_host: str = "127.0.0.1"
    biostar_local_port: int = 443
    biostar_local_scheme: Literal["http", "https"] = "https"
    biostar_local_user: str = ""
    biostar_local_password: str = ""
    # Archivo JSON donde el monitor local publica el último evento y que el
    # endpoint GET /biostar/events/latest expone a Ignition u otros consumidores.
    biostar_local_output_path: str = "C:/Users/Public/hgac_biostar_local.json"

    # --- RNTT (portal/stub legacy; alimenta /crossing/evaluate) ---
    rntt_portal_url: str = ""
    rntt_timeout_seconds: int = 30
    rntt_headless: bool = True
    rntt_use_stub: bool = True

    # --- RNTT API ASMX (consulta real chofer/camión) ---
    # OBLIGATORIOS: el WebService exige autenticación (sin credenciales responde
    # "No tiene acceso a este servicio"). Si falta cualquiera, los endpoints de
    # integración RNTT devuelven 503 (configuración incompleta).
    rntt_base_url: str = ""
    rntt_username: str = ""
    rntt_password: str = ""
    # `rntt_timeout_seconds` (sección legacy arriba) es opcional; default 30.
    # Modo de autenticación confirmado en producción: headers Username/Password.
    # `hmac` arma Username/Time/Token = HMAC_SHA256(user+time, key=password).
    rntt_auth_mode: Literal["header", "hmac"] = "header"
    # DEBUG: habilita los intentos de diagnóstico del script original (incluye
    # combinaciones inseguras como no-auth). Apagado por defecto.
    rntt_enable_diagnostic_fallbacks: bool = False

    # --- Navis (API interna HIT, OAuth password grant) ---
    navis_api_base: str = ""
    navis_token_url: str = ""
    navis_token_path: str = "oauth/token"
    navis_grant_type: str = "password"
    navis_client_id: str = ""
    navis_client_secret: str = ""
    navis_username: str = ""
    navis_password: str = ""
    navis_scope: str = ""
    navis_timeout_seconds: int = 25

    # --- Wialon (GPS Gurtam; nube o local de HIT) ---
    wialon_token: str = ""
    wialon_host: str = "https://hst-api.wialon.com"
    wialon_timeout_seconds: int = 15
    # Edad máxima del último reporte GPS (s) para considerar la unidad "online".
    wialon_online_seconds: int = 300
    # Geocercas (CSV) que cuentan como "terminal" y palabras clave de zona de gate.
    wialon_terminal_geofence_names: str = "TERMINAL GENERAL,TERMINAL"
    wialon_gate_zone_keywords: str = "GATE,ENTRADA,SALIDA,GARITA,CARRIL,ACCESO,RUTA ENTRADA"

    # --- Ignition ---
    ignition_json_output_dir: str = "./data/ignition_outbox"
    # Archivo fijo del último LPR que el gateway de Ignition lee en bucle. Se
    # escribe de forma atómica (ver IgnitionJsonWriter.write_lpr_latest).
    ignition_lpr_latest_path: str = "C:/Users/Public/hgac_lpr.json"
    ignition_base_url: str = "http://localhost:8088"
    ignition_event_endpoint: str = "/system/webdev/hgac/events/vehicle-observation"
    ignition_api_token: str = "change-me"
    ignition_timeout_seconds: int = 5

    # --- Monitor web ---
    monitor_fps : int = 20
    monitor_jpeg_quality: int = 75
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def biostar_base_url(self) -> str:
        scheme = "https" if self.biostar_port == 443 else "http"
        return f"{scheme}://{self.biostar_host}:{self.biostar_port}"

    @property
    def biostar_local_base_url(self) -> str:
        return f"{self.biostar_local_scheme}://{self.biostar_local_host}:{self.biostar_local_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()