"""Configuración centralizada del backend.

Todas las variables se cargan desde `.env` (o entorno real en producción).
Nunca debe haber credenciales o hosts hardcodeados fuera de este módulo.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    lpr_engine: str = "opencv_easyocr_poc"
    # Escala 0-100; validado para evitar confundirlo con el legacy (0-1).
    lpr_read_min_confidence: float = Field(default=70.0, ge=0, le=100)
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

    # --- Cámara ---
    camera_provider: Literal["webcam", "rtsp"] = "webcam"
    webcam_index: int = 0
    rtsp_url: str = ""
    camera_capture_timeout_seconds: int = 5

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

    # --- RNTT (portal/stub legacy; alimenta /crossing/evaluate) ---
    rntt_portal_url: str = ""
    rntt_timeout_seconds: int = 30
    rntt_headless: bool = True
    rntt_use_stub: bool = True

    # --- RNTT API ASMX (consulta real chofer/camión) ---
    rntt_base_url: str = ""
    rntt_username: str = ""
    rntt_password: str = ""
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