"""Tests del recorte por ROI de cámara antes del OCR (POST /api/v1/lpr/reads).

El frame completo SIEMPRE se guarda como evidencia; el motor LPR debe recibir
solo el recorte definido por `lpr_roi` en la cámara. Cuando la cámara no define
ROI, el motor recibe el frame completo y la respuesta trae `camera_roi: null`.

Se prueba en dos niveles:
- `_apply_camera_lpr_roi` directamente (incluye acotado a bordes y ROI fuera de cuadro).
- `LprService.read_plate` con una cámara stub y un motor que graba qué imagen recibió.
"""

import cv2
import numpy as np

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.modules.camera.camera_registry import CameraConfig
from app.modules.lpr.lpr_models import LprReadRequest
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService, _apply_camera_lpr_roi
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateValidator


FRAME_W, FRAME_H = 1920, 1080


def _make_image() -> np.ndarray:
    return np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)


def _jpeg_bytes(image: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


def _config(**roi) -> CameraConfig:
    return CameraConfig(
        camera_id="CAM-HIT-LPR-01",
        camera_name="HIT LPR",
        source_type="rtsp",
        source="",
        roi_x=roi.get("x", 0),
        roi_y=roi.get("y", 0),
        roi_width=roi.get("width", 0),
        roi_height=roi.get("height", 0),
    )


# --- _apply_camera_lpr_roi (lógica de recorte) ---


def test_apply_roi_returns_full_frame_when_no_roi() -> None:
    image = _make_image()
    result, applied = _apply_camera_lpr_roi(image, _config())
    assert applied is None
    assert result.shape == image.shape
    assert result is image  # no se copia ni recorta


def test_apply_roi_crops_to_configured_region() -> None:
    image = _make_image()
    result, applied = _apply_camera_lpr_roi(
        image, _config(x=100, y=60, width=800, height=400)
    )
    assert applied == {"x": 100, "y": 60, "width": 800, "height": 400}
    assert result.shape == (400, 800, 3)


def test_apply_roi_is_clamped_to_frame_bounds() -> None:
    # ROI que se sale por la derecha/abajo: se acota al tamaño real del frame.
    image = _make_image()
    result, applied = _apply_camera_lpr_roi(
        image, _config(x=1800, y=1000, width=500, height=500)
    )
    assert applied == {"x": 1800, "y": 1000, "width": 120, "height": 80}
    assert result.shape == (80, 120, 3)


def test_apply_roi_outside_frame_falls_back_to_full_frame() -> None:
    # ROI completamente fuera de cuadro: no se entrega una imagen vacía al OCR.
    image = _make_image()
    result, applied = _apply_camera_lpr_roi(
        image, _config(x=5000, y=5000, width=100, height=100)
    )
    assert applied is None
    assert result is image


# --- LprService.read_plate (el OCR recibe el recorte; evidencia = frame completo) ---


class _StubCamera:
    """Solo lo que usa LprService: capturar frame y resolver config."""

    def __init__(self, frame_bytes: bytes, config: CameraConfig) -> None:
        self._frame = frame_bytes
        self._config = config

    def capture_current_frame(self, camera_id: str) -> bytes:
        return self._frame

    def get_config(self, camera_id: str) -> CameraConfig:
        return self._config


class _RecordingEngine(LprEngine):
    """Graba la forma de la imagen recibida para verificar el recorte."""

    def __init__(self) -> None:
        self.received_shape: tuple[int, ...] | None = None

    @property
    def name(self) -> str:
        return "recording_poc"

    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        self.received_shape = frame_bgr.shape
        return LprEngineResult(
            best_raw_text=None,
            best_normalized_text=None,
            confidence=0.0,
            plate_crop_jpeg=None,
        )


def _service(tmp_path, camera: _StubCamera, engine: _RecordingEngine) -> LprService:
    return LprService(
        camera_service=camera,
        engine=engine,
        storage=LprResultStorage(
            base_path=str(tmp_path / "lpr"),
            public_base_url="http://localhost:8000/evidence",
        ),
        normalizer=PlateNormalizer(),
        validator=PlateValidator(),
    )


def test_engine_receives_cropped_frame_and_response_reports_roi(tmp_path) -> None:
    image = _make_image()
    camera = _StubCamera(
        _jpeg_bytes(image), _config(x=100, y=60, width=800, height=400)
    )
    engine = _RecordingEngine()
    service = _service(tmp_path, camera, engine)

    response = service.read_plate(LprReadRequest(camera_id="CAM-HIT-LPR-01"))

    # El OCR corrió sobre el recorte, no sobre el frame 1920x1080.
    assert engine.received_shape == (400, 800, 3)
    assert response.camera_roi is not None
    assert response.camera_roi.model_dump() == {
        "x": 100,
        "y": 60,
        "width": 800,
        "height": 400,
    }
    # La evidencia es el frame COMPLETO, no el recorte.
    frame_files = sorted((tmp_path / "lpr" / "frames").glob("*.jpg"))
    assert len(frame_files) == 1
    saved = cv2.imread(str(frame_files[0]))
    assert saved.shape == (FRAME_H, FRAME_W, 3)


def test_engine_receives_full_frame_when_camera_has_no_roi(tmp_path) -> None:
    image = _make_image()
    camera = _StubCamera(_jpeg_bytes(image), _config())  # sin ROI
    engine = _RecordingEngine()
    service = _service(tmp_path, camera, engine)

    response = service.read_plate(LprReadRequest(camera_id="CAM-HIT-LPR-01"))

    assert engine.received_shape == (FRAME_H, FRAME_W, 3)
    assert response.camera_roi is None
