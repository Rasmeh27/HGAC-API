"""Integración del catálogo dominicano en `LprService` (sin EasyOCR ni cámara real).

Se inyecta una cámara y un motor falsos: la cámara devuelve un JPEG de prueba y el
motor un candidato configurable. Verifica que la respuesta incluye la clasificación
de placa, que las inválidas se rechazan y que un conflicto G237627/G737627 con
scores cercanos NO se acepta automáticamente.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.modules.camera.camera_registry import CameraConfig
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.lpr_models import LprReadRequest, LprReadStatus
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateValidator

_SAMPLE_JPEG = cv2.imencode(".jpg", np.zeros((480, 640, 3), dtype=np.uint8))[1].tobytes()
_CLEAN = re.compile(r"[^A-Z0-9]")


class _FakeCamera:
    """Solo implementa lo que usa LprService: capture_current_frame + get_config."""

    def capture_current_frame(self, camera_id: str) -> bytes:
        return _SAMPLE_JPEG

    def get_config(self, camera_id: str) -> CameraConfig:
        # Sin lpr_roi: el OCR usa el frame completo (comportamiento legacy).
        return CameraConfig(
            camera_id=camera_id,
            camera_name=camera_id,
            source_type="rtsp",
            source="",
        )


class _FakeEngine(LprEngine):
    def __init__(self, raw_text: str | None, confidence: float, scores=()) -> None:
        self._raw = raw_text
        self._confidence = confidence
        self._scores = tuple(scores)

    @property
    def name(self) -> str:
        return "fake_engine"

    def read_plate(self, frame_bgr) -> LprEngineResult:
        normalized = _CLEAN.sub("", self._raw.upper()) if self._raw else None
        return LprEngineResult(
            best_raw_text=self._raw,
            best_normalized_text=normalized,
            confidence=self._confidence,
            plate_crop_jpeg=_SAMPLE_JPEG if self._raw else None,
            candidate_count=len(self._scores) or (1 if self._raw else 0),
            candidate_scores=self._scores,
        )


def _service(tmp_path, engine: _FakeEngine, *, catalog: bool = True) -> LprService:
    return LprService(
        camera_service=_FakeCamera(),
        engine=engine,
        storage=LprResultStorage(
            base_path=str(tmp_path / "lpr"),
            public_base_url="http://localhost:8000/evidence",
        ),
        normalizer=PlateNormalizer(),
        validator=PlateValidator(),
        min_confidence=70.0,
        catalog=DominicanPlatePatternCatalog() if catalog else None,
        ambiguous_min_score_delta=15.0,
        ambiguous_candidate_distance=1,
    )


def _read(service: LprService):
    return service.read_plate(LprReadRequest(camera_id="CAM-P-01"))


def test_response_includes_classification(tmp_path) -> None:
    service = _service(tmp_path, _FakeEngine("A123456", 88.0))
    body = _read(service)
    assert body.status is LprReadStatus.PLATE_DETECTED
    assert body.plate == "A123456"
    assert body.plate_normalized == "A123456"
    assert body.plate_type == "PRIVATE_AUTO"
    assert body.vehicle_type == "automovil_privado"
    assert body.format_pattern  # patrón regex presente
    assert body.format_valid is True
    assert body.expected_format == "DOMINICAN_PLATE_CATALOG"


def test_official_plate_classified(tmp_path) -> None:
    body = _read(_service(tmp_path, _FakeEngine("OF12345", 90.0)))
    assert body.status is LprReadStatus.PLATE_DETECTED
    assert body.plate_type == "OFICIAL"
    assert body.vehicle_type == "oficial"


def test_invalid_plate_is_rejected_with_clear_reason(tmp_path) -> None:
    body = _read(_service(tmp_path, _FakeEngine("1234567", 95.0)))
    assert body.status is LprReadStatus.FORMAT_MISMATCH
    assert body.plate is None
    assert body.plate_normalized is None
    assert body.rejection_reason == "format_mismatch"
    assert body.plate_type == "UNKNOWN"


def test_ambiguous_g237627_vs_g737627_not_accepted(tmp_path) -> None:
    scores = [
        {"normalized_text": "G237627", "score": 80.0, "confidence": 90.0},
        {"normalized_text": "G737627", "score": 78.0, "confidence": 89.0},
    ]
    body = _read(_service(tmp_path, _FakeEngine("G237627", 90.0, scores=scores)))
    assert body.status is LprReadStatus.AMBIGUOUS_READ
    assert body.plate is None  # no se acepta automáticamente
    assert body.rejection_reason == "ambiguous_digit_conflict"
    # Los candidatos quedan enriquecidos con su clasificación para depuración.
    assert all(s.get("plate_type") == "JEEPETA" for s in body.candidate_scores)


def test_strong_score_delta_resolves_ambiguity(tmp_path) -> None:
    # Mismo conflicto pero con score muy superior: se acepta el ganador.
    scores = [
        {"normalized_text": "G237627", "score": 95.0, "confidence": 92.0},
        {"normalized_text": "G737627", "score": 60.0, "confidence": 70.0},
    ]
    body = _read(_service(tmp_path, _FakeEngine("G237627", 92.0, scores=scores)))
    assert body.status is LprReadStatus.PLATE_DETECTED
    assert body.plate == "G237627"


def test_catalog_disabled_keeps_legacy_behavior(tmp_path) -> None:
    # Sin catálogo: validación por PlateValidator (LETTER_6_DIGITS), sin clasificación.
    service = _service(tmp_path, _FakeEngine("A123456", 88.0), catalog=False)
    body = _read(service)
    assert body.status is LprReadStatus.PLATE_DETECTED
    assert body.plate_type is None
    assert body.vehicle_type is None
    assert body.expected_format == "LETTER_6_DIGITS"
