"""Modelos request/response del módulo LPR (contrato HTTP)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LprReadStatus(str, Enum):
    PLATE_DETECTED = "PLATE_DETECTED"
    NO_PLATE_DETECTED = "NO_PLATE_DETECTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    FORMAT_MISMATCH = "FORMAT_MISMATCH"
    ERROR = "ERROR"


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
    preprocessing_variant: str | None = None
    crop_saved: bool = False
    selected_roi: str | None = None
    digit_count: int = 0
    alpha_count: int = 0
    candidate_rejections: list[dict] = Field(default_factory=list)
    candidate_scores: list[dict] = Field(default_factory=list)
