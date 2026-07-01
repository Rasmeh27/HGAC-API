"""Modelos request/response del módulo LPR (contrato HTTP)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class CameraRoi(BaseModel):
    """Región de interés (en píxeles del frame completo) aplicada antes del OCR.

    Refleja el ROI REALMENTE recortado: si el ROI configurado se sale del frame,
    estos valores vienen acotados a los límites de la imagen.
    """

    x: int
    y: int
    width: int
    height: int


class LprReadStatus(str, Enum):
    PLATE_DETECTED = "PLATE_DETECTED"
    NO_PLATE_DETECTED = "NO_PLATE_DETECTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    # Dos candidatos válidos casi idénticos (difieren en un carácter) con scores
    # cercanos: no se acepta automáticamente, requiere más evidencia.
    AMBIGUOUS_READ = "AMBIGUOUS_READ"
    # Ráfaga multiframe sin ningún frame utilizable (todos borrosos/quemados):
    # no se pudo intentar una lectura confiable.
    BLURRY_FRAME = "BLURRY_FRAME"
    ERROR = "ERROR"


class RotuloReadStatus(str, Enum):
    """Estado de la lectura de RÓTULO (independiente del de placa)."""

    ROTULO_DETECTED = "ROTULO_DETECTED"
    NO_ROTULO_DETECTED = "NO_ROTULO_DETECTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    ERROR = "ERROR"
    # La cámara no tiene rotulo_roi configurado: no se intentó leer rótulo.
    NOT_CONFIGURED = "NOT_CONFIGURED"


class PlateCandidate(BaseModel):
    """Candidato OCR crudo/normalizado, expuesto AUNQUE sea rechazado.

    Permite depurar qué leyó el motor (SimpleLPR o EasyOCR) aunque no haya una
    placa/rótulo aceptado. `bbox` es opcional (lo aporta SimpleLPR si detecta
    región); `rejection_reason` indica por qué no se aceptó.
    """

    engine: str
    raw_text: str | None = None
    normalized_text: str | None = None
    confidence: float = 0.0
    format_valid: bool = False
    bbox: CameraRoi | None = None
    source: str | None = None
    substitutions: int | None = None
    rejection_reason: str | None = None
    # --- Trazabilidad multiframe (null en lectura de un solo frame) ---
    frame_index: int | None = None
    frame_quality_score: float | None = None
    sharpness: float | None = None
    brightness: float | None = None


class EngineAttempt(BaseModel):
    """Intento de un motor en una lectura (para trazar auto/fallback).

    status: OK | NO_DETECTION | ERROR | NOT_USED | UNAVAILABLE.
    """

    engine: str
    status: str
    error: str | None = None


class LprReadRequest(BaseModel):
    """Solicitud de lectura. Solo `camera_id` es obligatorio.

    `event_id` es opcional: si no se envía, el servicio genera uno. El resto son
    metadatos de contexto (terminal/zona/acceso/carril) que se registran en el
    log; en esta fase no se persisten en base de datos ni colas.
    """

    camera_id: str = Field(..., min_length=1)
    terminal: str | None = None
    zone: str | None = None
    access: str | None = None
    lane: str | None = None
    event_id: str | None = None
    requested_by: str | None = None


class LprReadResponse(BaseModel):
    event_id: str
    camera_id: str
    camera_name: str = ""
    camera_ip: str = ""
    status: LprReadStatus
    plate: str | None = None
    plate_normalized: str | None = None
    confidence: float = 0.0
    source_frame_path: str
    source_frame_url: str
    plate_crop_path: str | None = None
    plate_crop_url: str | None = None
    processing_time_ms: int
    detected_at: datetime
    engine: str

    # --- Depuración: por qué la lectura fue (o no) aceptada ---
    candidate_count: int = 0
    ocr_attempt_count: int = 0
    best_raw_text: str | None = None
    best_normalized_text: str | None = None
    expected_format: str | None = None
    format_valid: bool = False
    rejection_reason: str | None = None

    # --- Clasificación de placa (catálogo dominicano; null si el catálogo está off
    #     o si no hubo candidato). No reemplaza validación contra RNTT/Navis. ---
    plate_type: str | None = None
    vehicle_type: str | None = None
    format_pattern: str | None = None
    preprocessing_variant: str | None = None
    crop_saved: bool = False
    selected_roi: str | None = None
    # ROI de la cámara (config) recortado del frame antes de correr el OCR. Null
    # cuando la cámara no define ROI: en ese caso el OCR usó el frame completo.
    # No confundir con `selected_roi`, que es la región interna que elige el motor.
    camera_roi: CameraRoi | None = None
    digit_count: int = 0
    alpha_count: int = 0
    candidate_rejections: list[dict] = Field(default_factory=list)
    candidate_scores: list[dict] = Field(default_factory=list)
    frames_requested: int = 1
    frames_captured: int = 1
    frames_processed: int = 1
    consensus_votes: int = 0
    consensus_total: int = 0
    consensus_ratio: float = 0.0
    frame_candidates: list[dict] = Field(default_factory=list)

    # ===================================================================
    # Contrato EXTENDIDO (placa + rótulo + multi-motor + evidencia debug).
    # Todos los campos legacy de arriba SE MANTIENEN para no romper Ignition;
    # estos son aditivos y opcionales.
    # ===================================================================

    # --- Placa (espejo estructurado de los campos legacy de placa) ---
    plate_status: LprReadStatus | None = None
    plate_engine: str | None = None
    plate_candidates: list[PlateCandidate] = Field(default_factory=list)

    # --- Rótulo de camión (lectura independiente sobre rotulo_roi) ---
    rotulo: str | None = None
    rotulo_normalized: str | None = None
    rotulo_confidence: float = 0.0
    rotulo_status: RotuloReadStatus | None = None
    rotulo_engine: str | None = None
    rotulo_format_valid: bool = False
    rotulo_rejection_reason: str | None = None
    rotulo_candidates: list[PlateCandidate] = Field(default_factory=list)
    rotulo_roi: CameraRoi | None = None
    rotulo_crop_path: str | None = None
    rotulo_crop_url: str | None = None

    # --- Selección de motor (auto/fallback) ---
    engine_attempts: list[EngineAttempt] = Field(default_factory=list)

    # --- Ráfaga multiframe (consenso). En lectura de un solo frame: count=1. ---
    burst_frame_count: int = 0
    processed_frame_count: int = 0
    usable_frame_count: int = 0
    best_frame_index: int | None = None
    best_frame_sharpness: float = 0.0
    best_frame_brightness: float = 0.0
    burst_frame_urls: list[str] = Field(default_factory=list)

    # --- Evidencia de depuración (para calibrar ROI desde Ignition) ---
    debug_frame_url: str | None = None
    plate_roi_url: str | None = None
    rotulo_roi_url: str | None = None
    roi_overlay_url: str | None = None
