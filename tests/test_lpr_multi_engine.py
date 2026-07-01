"""Selección de motor en runtime (auto/fallback) y `engine_attempts`.

Prueba a nivel de servicio (motores inyectados, sin SimpleLPR/EasyOCR real):
- candidatos expuestos aunque la placa sea rechazada;
- fallback cuando el primario lanza o no detecta;
- `engine_attempts` refleja OK/ERROR/NO_DETECTION/NOT_USED/UNAVAILABLE;
- etiqueta `engine` combinada cuando se usó el fallback.
"""

from __future__ import annotations

from app.modules.lpr.lpr_models import LprReadRequest
from tests._lpr_service_fakes import (
    FixedEngine,
    RaisingEngine,
    StubCamera,
    build_service,
    jpeg_bytes,
    make_config,
    make_image,
)

_REQUEST = LprReadRequest(camera_id="CAM-HIT-LPR-01")


def _camera():
    return StubCamera(jpeg_bytes(make_image()), make_config())


def test_single_engine_attempt_is_ok_and_engine_name_preserved(tmp_path) -> None:
    engine = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0)
    service = build_service(tmp_path, _camera(), engine)

    body = service.read_plate(_REQUEST)

    assert body.status.value == "PLATE_DETECTED"
    assert body.engine == "opencv_easyocr_poc"
    assert body.plate_engine == "opencv_easyocr_poc"
    assert [a.engine for a in body.engine_attempts] == ["opencv_easyocr_poc"]
    assert body.engine_attempts[0].status == "OK"


def test_candidates_exposed_even_when_plate_rejected(tmp_path) -> None:
    # "460432" no cumple formato dominicano -> FORMAT_MISMATCH, pero el candidato
    # debe verse en plate_candidates (no se pierde lo que leyó el motor).
    engine = FixedEngine("opencv_easyocr_poc", raw_text="460432", confidence=90.0)
    service = build_service(tmp_path, _camera(), engine)

    body = service.read_plate(_REQUEST)

    assert body.status.value == "FORMAT_MISMATCH"
    assert body.plate is None
    raws = {c.raw_text for c in body.plate_candidates}
    assert "460432" in raws


def test_simplelpr_like_candidates_are_mapped(tmp_path) -> None:
    # Motor estilo SimpleLPR: entrega candidate_scores con texto/normalizado/score.
    scores = (
        {
            "text": "L453933",
            "normalized_text": "L453933",
            "ocr_text": "L453933",
            "confidence": 86.0,
            "source": "exact",
            "substitutions": 0,
        },
    )
    engine = FixedEngine(
        "simplelpr_rd_poc", raw_text="L453933", confidence=86.0, candidate_scores=scores
    )
    service = build_service(tmp_path, _camera(), engine, engine_mode="simplelpr_rd_poc")

    body = service.read_plate(_REQUEST)

    assert body.status.value == "PLATE_DETECTED"
    assert body.plate == "L453933"
    assert body.plate_type == "CARGA"  # catálogo dominicano: L + 6 dígitos
    assert body.plate_candidates
    assert body.plate_candidates[0].engine == "simplelpr_rd_poc"
    assert body.plate_candidates[0].normalized_text == "L453933"


def test_fallback_used_when_primary_raises(tmp_path) -> None:
    primary = RaisingEngine("simplelpr_rd_poc")
    fallback = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0)
    service = build_service(
        tmp_path, _camera(), primary, fallback_engine=fallback, engine_mode="auto"
    )

    body = service.read_plate(_REQUEST)

    assert body.status.value == "PLATE_DETECTED"
    assert body.plate == "L460432"
    assert body.plate_engine == "opencv_easyocr_poc"
    # Etiqueta legacy combinada para Ignition.
    assert body.engine == "simplelpr_rd_poc+opencv_easyocr_poc_fallback"
    statuses = {a.engine: a.status for a in body.engine_attempts}
    assert statuses["simplelpr_rd_poc"] == "ERROR"
    assert statuses["opencv_easyocr_poc"] == "OK"
    assert primary.call_count == 1 and fallback.call_count == 1


def test_fallback_used_when_primary_has_no_detection(tmp_path) -> None:
    primary = FixedEngine("simplelpr_rd_poc", raw_text=None)
    fallback = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0)
    service = build_service(
        tmp_path, _camera(), primary, fallback_engine=fallback, engine_mode="auto"
    )

    body = service.read_plate(_REQUEST)

    assert body.plate == "L460432"
    statuses = {a.engine: a.status for a in body.engine_attempts}
    assert statuses["simplelpr_rd_poc"] == "NO_DETECTION"
    assert statuses["opencv_easyocr_poc"] == "OK"


def test_fallback_not_used_when_primary_detects(tmp_path) -> None:
    primary = FixedEngine("simplelpr_rd_poc", raw_text="L460432", confidence=88.0)
    fallback = FixedEngine("opencv_easyocr_poc", raw_text="X999999", confidence=99.0)
    service = build_service(
        tmp_path, _camera(), primary, fallback_engine=fallback, engine_mode="auto"
    )

    body = service.read_plate(_REQUEST)

    assert body.plate == "L460432"
    assert body.engine == "simplelpr_rd_poc"  # no se combinó: no hubo fallback
    statuses = {a.engine: a.status for a in body.engine_attempts}
    assert statuses["opencv_easyocr_poc"] == "NOT_USED"
    assert fallback.call_count == 0


def test_unavailable_engine_is_reported_in_attempts(tmp_path) -> None:
    # Modo auto degradado a OpenCV: SimpleLPR no se pudo construir.
    engine = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0)
    service = build_service(
        tmp_path,
        _camera(),
        engine,
        engine_mode="auto",
        unavailable_engines=[
            {"engine": "simplelpr_rd_poc", "status": "UNAVAILABLE", "error": "not installed"}
        ],
    )

    body = service.read_plate(_REQUEST)

    statuses = {a.engine: a.status for a in body.engine_attempts}
    assert statuses["simplelpr_rd_poc"] == "UNAVAILABLE"
    assert statuses["opencv_easyocr_poc"] == "OK"


def test_all_engines_error_returns_error_status(tmp_path) -> None:
    primary = RaisingEngine("simplelpr_rd_poc")
    fallback = RaisingEngine("opencv_easyocr_poc")
    service = build_service(
        tmp_path, _camera(), primary, fallback_engine=fallback, engine_mode="auto"
    )

    body = service.read_plate(_REQUEST)

    assert body.status.value == "ERROR"
    assert body.rejection_reason == "engine_error"
    statuses = [a.status for a in body.engine_attempts]
    assert statuses == ["ERROR", "ERROR"]
