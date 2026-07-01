"""Ráfaga multiframe + consenso en POST /api/v1/lpr/reads (nivel servicio).

Cubre: captura de ráfaga, descarte de frames borrosos, consenso (2 votos / un
frame de alta confianza), exposición de candidatos rechazados, BLURRY_FRAME
cuando todo está borroso, fallback multi-motor en ráfaga y campos legacy.
"""

from __future__ import annotations

from app.modules.lpr.lpr_models import LprReadRequest, LprReadStatus
from tests._lpr_service_fakes import (
    BurstStubCamera,
    FixedEngine,
    RaisingEngine,
    build_service,
    jpeg_bytes,
    make_blur_image,
    make_config,
    make_sharp_image,
)

_REQUEST = LprReadRequest(camera_id="CAM-HIT-LPR-01")
PLATE_ROI = (100, 100, 400, 300)
FRAME_W, FRAME_H = 800, 600


def _sharp():
    return jpeg_bytes(make_sharp_image(FRAME_W, FRAME_H))


def _blur():
    return jpeg_bytes(make_blur_image(FRAME_W, FRAME_H))


def _burst_camera(frames):
    return BurstStubCamera(frames, make_config(plate_roi=PLATE_ROI))


def _jpgs(directory):
    return sorted(directory.glob("*.jpg")) if directory.exists() else []


def _burst_service(tmp_path, camera, engine, *, top=5, min_conf=55.0, votes=2, **kw):
    return build_service(
        tmp_path,
        camera,
        engine,
        burst_frame_count=12,
        burst_top_frames=top,
        min_confidence=min_conf,
        consensus_min_votes=votes,
        single_frame_accept_confidence=75.0,
        **kw,
    )


# ------------------------------------------------------------------
# Consenso (unidad): _select_best_plate_candidate
# ------------------------------------------------------------------


def _cand(text, conf, frame_index, quality=0.9):
    return {
        "engine": "simplelpr_rd_poc",
        "raw_text": text,
        "normalized_text": text,
        "confidence": conf,
        "frame_index": frame_index,
        "frame_quality_score": quality,
    }


def _svc(tmp_path):
    return _burst_service(tmp_path, _burst_camera([_sharp()]), FixedEngine("x", None))


def test_consensus_accepts_valid_plate_with_two_votes(tmp_path) -> None:
    svc = _svc(tmp_path)
    cands = [_cand("L453933", 60.0, 0), _cand("L453933", 58.0, 1)]
    cons = svc._select_best_plate_candidate(cands, processed_count=5)
    assert cons.status == LprReadStatus.PLATE_DETECTED
    assert cons.accepted is True
    assert cons.votes == 2


def test_consensus_accepts_single_high_confidence_frame(tmp_path) -> None:
    svc = _svc(tmp_path)
    cons = svc._select_best_plate_candidate([_cand("L453933", 80.0, 3)], processed_count=5)
    assert cons.status == LprReadStatus.PLATE_DETECTED
    assert cons.accepted is True
    assert cons.votes == 1


def test_consensus_valid_but_low_and_single_is_low_confidence(tmp_path) -> None:
    # Válida, un solo voto, confianza < single_accept(75) y >= min(55): LOW_CONFIDENCE.
    svc = _svc(tmp_path)
    cons = svc._select_best_plate_candidate([_cand("L453933", 60.0, 0)], processed_count=5)
    assert cons.status == LprReadStatus.LOW_CONFIDENCE
    assert cons.accepted is False


def test_consensus_rejects_format_mismatch(tmp_path) -> None:
    svc = _svc(tmp_path)
    cands = [_cand("460432", 95.0, 0), _cand("460432", 95.0, 1), _cand("460432", 95.0, 2)]
    cons = svc._select_best_plate_candidate(cands, processed_count=5)
    assert cons.status == LprReadStatus.FORMAT_MISMATCH
    assert cons.accepted is False


def test_consensus_no_candidates_is_no_plate(tmp_path) -> None:
    svc = _svc(tmp_path)
    cons = svc._select_best_plate_candidate([], processed_count=5)
    assert cons.status == LprReadStatus.NO_PLATE_DETECTED
    assert cons.accepted is False


# ------------------------------------------------------------------
# Ráfaga end-to-end
# ------------------------------------------------------------------


def test_burst_processes_top_frames_and_reaches_consensus(tmp_path) -> None:
    frames = [_sharp() for _ in range(12)]
    engine = FixedEngine("simplelpr_rd_poc", raw_text="L453933", confidence=86.0)
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5)

    body = svc.read_plate(_REQUEST)

    assert body.status == LprReadStatus.PLATE_DETECTED
    assert body.plate == "L453933" and body.plate_type == "CARGA"
    assert body.burst_frame_count == 12
    assert body.processed_frame_count == 5
    assert body.usable_frame_count == 12
    assert engine.call_count == 5  # SimpleLPR llamado 1x por top frame
    assert body.consensus_votes == 5 and body.consensus_total == 5
    assert body.consensus_ratio == 1.0
    assert body.best_frame_index is not None
    assert body.best_frame_sharpness > 80
    # Candidatos con trazabilidad de frame.
    assert body.plate_candidates
    c = body.plate_candidates[0]
    assert c.frame_index is not None and c.sharpness is not None
    assert c.frame_quality_score is not None
    # Evidencia: SOLO el mejor frame (no 12 archivos).
    assert len(_jpgs(tmp_path / "lpr" / "frames")) == 1


def test_burst_discards_blurry_frames(tmp_path) -> None:
    frames = [_sharp(), _sharp(), _sharp()] + [_blur(), _blur(), _blur(), _blur()]
    engine = FixedEngine("simplelpr_rd_poc", raw_text="L453933", confidence=86.0)
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5)

    body = svc.read_plate(_REQUEST)

    assert body.usable_frame_count == 3  # 4 borrosos descartados
    assert body.processed_frame_count == 3  # solo se procesan los utilizables
    assert engine.call_count == 3
    assert body.status == LprReadStatus.PLATE_DETECTED


def test_burst_all_blurry_returns_blurry_frame(tmp_path) -> None:
    frames = [_blur() for _ in range(5)]
    engine = FixedEngine("simplelpr_rd_poc", raw_text=None)  # sin texto
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5)

    body = svc.read_plate(_REQUEST)

    assert body.usable_frame_count == 0
    assert body.status == LprReadStatus.BLURRY_FRAME
    assert body.plate is None


def test_burst_exposes_rejected_candidates(tmp_path) -> None:
    frames = [_sharp() for _ in range(6)]
    engine = FixedEngine("simplelpr_rd_poc", raw_text="460432", confidence=90.0)
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5)

    body = svc.read_plate(_REQUEST)

    assert body.status == LprReadStatus.FORMAT_MISMATCH
    assert body.plate is None
    assert {c.raw_text for c in body.plate_candidates} == {"460432"}


def test_burst_single_usable_high_conf_accepts(tmp_path) -> None:
    # 1 nítido + 4 borrosos: 1 solo frame utilizable, pero confianza alta -> acepta.
    frames = [_sharp()] + [_blur() for _ in range(4)]
    engine = FixedEngine("simplelpr_rd_poc", raw_text="L453933", confidence=88.0)
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5, votes=2)

    body = svc.read_plate(_REQUEST)

    assert body.usable_frame_count == 1
    assert body.consensus_votes == 1  # un solo voto...
    assert body.status == LprReadStatus.PLATE_DETECTED  # ...pero conf 88 >= 75
    assert body.plate == "L453933"


def test_burst_keeps_legacy_fields(tmp_path) -> None:
    frames = [_sharp() for _ in range(12)]
    crop = jpeg_bytes(make_sharp_image(120, 48))
    engine = FixedEngine("simplelpr_rd_poc", raw_text="L453933", confidence=86.0, crop=crop)
    svc = _burst_service(tmp_path, _burst_camera(frames), engine, top=5)

    body = svc.read_plate(_REQUEST)
    dumped = body.model_dump()
    for legacy in (
        "plate", "plate_normalized", "confidence", "status", "source_frame_url",
        "plate_crop_url", "camera_roi", "plate_candidates", "engine_attempts",
        "best_raw_text", "candidate_scores",
    ):
        assert legacy in dumped
    assert body.camera_roi is not None
    assert body.plate_crop_url is not None  # crop del mejor frame


def test_burst_multi_engine_fallback(tmp_path) -> None:
    frames = [_sharp() for _ in range(6)]
    primary = RaisingEngine("simplelpr_rd_poc")
    fallback = FixedEngine("opencv_easyocr_poc", raw_text="L453933", confidence=86.0)
    svc = _burst_service(
        tmp_path,
        _burst_camera(frames),
        primary,
        fallback_engine=fallback,
        engine_mode="auto",
        top=5,
    )

    body = svc.read_plate(_REQUEST)

    assert body.status == LprReadStatus.PLATE_DETECTED
    assert body.plate == "L453933"
    assert body.engine == "simplelpr_rd_poc+opencv_easyocr_poc_fallback"
    statuses = {a.engine: a.status for a in body.engine_attempts}
    assert statuses["simplelpr_rd_poc"] == "ERROR"
    assert statuses["opencv_easyocr_poc"] == "OK"
