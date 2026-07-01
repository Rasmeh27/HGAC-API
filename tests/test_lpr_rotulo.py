"""Lectura de RÓTULO (independiente de la placa) + evidencia de depuración.

- El rótulo usa `rotulo_roi` y un validador propio (E204 = 1 letra + 3 dígitos);
  NO se valida como placa dominicana ni viceversa.
- El motor de rótulo recibe SOLO el recorte de `rotulo_roi`.
- Se guarda evidencia: recorte de ROI de placa, de rótulo y overlay del frame.
- Sin `rotulo_roi`, el rótulo queda NOT_CONFIGURED y no se intenta OCR.
"""

from __future__ import annotations

import cv2

from app.modules.lpr.lpr_models import LprReadRequest, RotuloReadStatus
from tests._lpr_service_fakes import (
    FixedEngine,
    StubCamera,
    build_service,
    jpeg_bytes,
    make_config,
    make_image,
)

_REQUEST = LprReadRequest(camera_id="CAM-HIT-LPR-01")
PLATE_ROI = (760, 300, 720, 430)
ROTULO_ROI = (0, 100, 800, 700)


def _jpgs(directory):
    return sorted(directory.glob("*.jpg")) if directory.exists() else []


def test_plate_and_rotulo_read_independently_with_evidence(tmp_path) -> None:
    camera = StubCamera(
        jpeg_bytes(make_image()), make_config(plate_roi=PLATE_ROI, rotulo_roi=ROTULO_ROI)
    )
    crop = jpeg_bytes(make_image(120, 48))
    plate_engine = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0, crop=crop)
    rotulo_engine = FixedEngine("opencv_easyocr_poc", raw_text="E204", confidence=85.0, crop=crop)
    service = build_service(tmp_path, camera, plate_engine, rotulo_engine=rotulo_engine)

    body = service.read_plate(_REQUEST)

    # Placa y rótulo, cada uno con su validación.
    assert body.status.value == "PLATE_DETECTED"
    assert body.plate == "L460432" and body.plate_type == "CARGA"
    assert body.rotulo_status == RotuloReadStatus.ROTULO_DETECTED
    assert body.rotulo == "E204" and body.rotulo_normalized == "E204"
    assert body.rotulo_format_valid is True
    assert body.rotulo_engine == "opencv_easyocr_poc"

    # Cada motor recibió SOLO su ROI (no el frame completo 1920x1080).
    assert plate_engine.received_shape == (430, 720, 3)
    assert rotulo_engine.received_shape == (700, 800, 3)

    # ROIs reportados.
    assert body.camera_roi.model_dump() == {"x": 760, "y": 300, "width": 720, "height": 430}
    assert body.rotulo_roi.model_dump() == {"x": 0, "y": 100, "width": 800, "height": 700}

    # Evidencia de depuración: URLs presentes...
    assert body.debug_frame_url == body.source_frame_url
    assert body.plate_roi_url and "/roi/" in body.plate_roi_url
    assert body.rotulo_roi_url and "/roi/" in body.rotulo_roi_url
    assert body.roi_overlay_url and "/overlay/" in body.roi_overlay_url
    assert body.rotulo_crop_url and "/crops/" in body.rotulo_crop_url

    # ...y archivos realmente escritos en disco.
    base = tmp_path / "lpr"
    assert len(_jpgs(base / "frames")) == 1
    assert len(_jpgs(base / "crops")) == 2  # plate_crop + rotulo_crop
    assert len(_jpgs(base / "roi")) == 2  # plate_roi + rotulo_roi
    assert len(_jpgs(base / "overlay")) == 1
    # El overlay es decodificable y del tamaño del frame completo.
    overlay = cv2.imread(str(_jpgs(base / "overlay")[0]))
    assert overlay.shape == (1080, 1920, 3)


def test_rotulo_not_configured_when_camera_has_no_rotulo_roi(tmp_path) -> None:
    camera = StubCamera(jpeg_bytes(make_image()), make_config(plate_roi=PLATE_ROI))
    plate_engine = FixedEngine("opencv_easyocr_poc", raw_text="L460432", confidence=88.0)
    rotulo_engine = FixedEngine("opencv_easyocr_poc", raw_text="E204", confidence=85.0)
    service = build_service(tmp_path, camera, plate_engine, rotulo_engine=rotulo_engine)

    body = service.read_plate(_REQUEST)

    assert body.rotulo_status == RotuloReadStatus.NOT_CONFIGURED
    assert body.rotulo is None
    assert body.rotulo_roi is None
    # El motor de rótulo no se invocó.
    assert rotulo_engine.received_shape is None


def test_plate_like_text_in_rotulo_roi_is_format_mismatch(tmp_path) -> None:
    # "L453933" es una placa válida, NO un rótulo: se rechaza como rótulo pero
    # queda visible en rotulo_candidates.
    camera = StubCamera(jpeg_bytes(make_image()), make_config(rotulo_roi=ROTULO_ROI))
    plate_engine = FixedEngine("opencv_easyocr_poc", raw_text=None)
    rotulo_engine = FixedEngine("opencv_easyocr_poc", raw_text="L453933", confidence=90.0)
    service = build_service(tmp_path, camera, plate_engine, rotulo_engine=rotulo_engine)

    body = service.read_plate(_REQUEST)

    assert body.rotulo_status == RotuloReadStatus.FORMAT_MISMATCH
    assert body.rotulo is None
    assert body.rotulo_format_valid is False
    assert {c.raw_text for c in body.rotulo_candidates} == {"L453933"}


def test_rotulo_e204_is_not_accepted_as_plate(tmp_path) -> None:
    # Mismo texto "E204": rechazado como PLACA (no es formato dominicano), aceptado
    # como RÓTULO. Demuestra validadores separados.
    camera = StubCamera(jpeg_bytes(make_image()), make_config(rotulo_roi=ROTULO_ROI))
    plate_engine = FixedEngine("opencv_easyocr_poc", raw_text="E204", confidence=95.0)
    rotulo_engine = FixedEngine("opencv_easyocr_poc", raw_text="E204", confidence=85.0)
    service = build_service(tmp_path, camera, plate_engine, rotulo_engine=rotulo_engine)

    body = service.read_plate(_REQUEST)

    assert body.status.value == "FORMAT_MISMATCH"  # E204 NO es placa
    assert body.plate is None
    assert body.rotulo_status == RotuloReadStatus.ROTULO_DETECTED  # E204 SÍ es rótulo
    assert body.rotulo == "E204"


def test_rotulo_low_confidence(tmp_path) -> None:
    camera = StubCamera(jpeg_bytes(make_image()), make_config(rotulo_roi=ROTULO_ROI))
    plate_engine = FixedEngine("opencv_easyocr_poc", raw_text=None)
    rotulo_engine = FixedEngine("opencv_easyocr_poc", raw_text="E204", confidence=40.0)
    service = build_service(tmp_path, camera, plate_engine, rotulo_engine=rotulo_engine)

    body = service.read_plate(_REQUEST)

    assert body.rotulo_status == RotuloReadStatus.LOW_CONFIDENCE
    assert body.rotulo is None
    assert body.rotulo_rejection_reason == "low_confidence"
