"""Dobles reutilizables para tests del `LprService` del módulo (sin cámara/OCR real).

Stub de cámara (capture_current_frame + get_config), motores LPR de prueba (uno
con resultado fijo que graba la forma recibida, uno que lanza, y uno con
candidatos al estilo SimpleLPR) y un builder de servicio.
"""

from __future__ import annotations

import re

import cv2
import numpy as np

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.modules.camera.camera_registry import CameraConfig
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateValidator, build_rotulo_formats

FRAME_W, FRAME_H = 1920, 1080
_CLEAN = re.compile(r"[^A-Z0-9]")


def make_image(width: int = FRAME_W, height: int = FRAME_H) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


def jpeg_bytes(image: np.ndarray) -> bytes:
    return cv2.imencode(".jpg", image)[1].tobytes()


def make_sharp_image(width: int = FRAME_W, height: int = FRAME_H) -> np.ndarray:
    """Tablero de ajedrez: muchos bordes -> alta varianza de Laplaciano (nítido).

    Sobrevive a JPEG y da brillo ~medio (dentro del rango utilizable).
    """
    image = np.zeros((height, width, 3), dtype=np.uint8)
    tile = 20
    for y in range(0, height, tile):
        for x in range(0, width, tile):
            if ((x // tile) + (y // tile)) % 2 == 0:
                image[y : y + tile, x : x + tile] = 255
    return image


def make_blur_image(width: int = FRAME_W, height: int = FRAME_H, value: int = 110) -> np.ndarray:
    """Imagen sólida: varianza de Laplaciano ~0 (borrosa/sin bordes)."""
    return np.full((height, width, 3), value, dtype=np.uint8)


def make_config(
    plate_roi: tuple[int, int, int, int] | None = None,
    rotulo_roi: tuple[int, int, int, int] | None = None,
    camera_id: str = "CAM-HIT-LPR-01",
) -> CameraConfig:
    kw: dict = {}
    if plate_roi:
        kw.update(
            roi_x=plate_roi[0],
            roi_y=plate_roi[1],
            roi_width=plate_roi[2],
            roi_height=plate_roi[3],
        )
    if rotulo_roi:
        kw.update(
            rotulo_roi_x=rotulo_roi[0],
            rotulo_roi_y=rotulo_roi[1],
            rotulo_roi_width=rotulo_roi[2],
            rotulo_roi_height=rotulo_roi[3],
        )
    return CameraConfig(
        camera_id=camera_id,
        camera_name="HIT LPR",
        source_type="rtsp",
        source="",
        **kw,
    )


class StubCamera:
    def __init__(self, frame_bytes: bytes, config: CameraConfig) -> None:
        self._frame = frame_bytes
        self._config = config

    def capture_current_frame(self, camera_id: str) -> bytes:
        return self._frame

    def get_config(self, camera_id: str) -> CameraConfig:
        return self._config


class BurstStubCamera:
    """Cámara con ráfaga: devuelve una lista fija de frames (sin sleep)."""

    def __init__(self, frames_bytes: list[bytes], config: CameraConfig) -> None:
        self._frames = list(frames_bytes)
        self._config = config

    def capture_frame_burst(
        self, camera_id: str, count: int = 5, interval_ms: int = 0
    ) -> list[bytes]:
        return self._frames[:count] if count else list(self._frames)

    def capture_current_frame(self, camera_id: str) -> bytes:
        return self._frames[0]

    def get_config(self, camera_id: str) -> CameraConfig:
        return self._config


class FixedEngine(LprEngine):
    """Devuelve un resultado fijo y graba la forma de la imagen recibida."""

    def __init__(
        self,
        name: str,
        raw_text: str | None,
        confidence: float = 0.0,
        crop: bytes | None = None,
        candidate_scores: tuple[dict, ...] = (),
    ) -> None:
        self._name = name
        self._raw = raw_text
        self._confidence = confidence
        self._crop = crop
        self._candidate_scores = candidate_scores
        self.received_shape: tuple[int, ...] | None = None
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        self.received_shape = frame_bgr.shape
        self.call_count += 1
        normalized = _CLEAN.sub("", self._raw.upper()) if self._raw else None
        return LprEngineResult(
            best_raw_text=self._raw,
            best_normalized_text=normalized,
            confidence=self._confidence,
            plate_crop_jpeg=self._crop,
            candidate_count=1 if self._raw else 0,
            candidate_scores=self._candidate_scores,
        )


class RaisingEngine(LprEngine):
    """Motor que siempre lanza (para probar fallback ante error de motor)."""

    def __init__(self, name: str, error: str = "boom") -> None:
        self._name = name
        self._error = error
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        self.call_count += 1
        raise RuntimeError(self._error)


def build_service(
    tmp_path,
    camera: StubCamera,
    engine: LprEngine,
    *,
    fallback_engine: LprEngine | None = None,
    rotulo_engine: LprEngine | None = None,
    engine_mode: str | None = None,
    unavailable_engines: list[dict] | None = None,
    catalog: bool = True,
    min_confidence: float = 70.0,
    rotulo_min_confidence: float = 60.0,
    save_debug_evidence: bool = True,
    # Ráfaga multiframe (por defecto single-frame para compatibilidad).
    burst_frame_count: int = 1,
    burst_interval_ms: int = 0,
    burst_top_frames: int = 1,
    min_frame_sharpness: float = 80.0,
    min_frame_brightness: float = 30.0,
    max_frame_brightness: float = 235.0,
    consensus_min_votes: int = 2,
    single_frame_accept_confidence: float = 75.0,
    save_burst_frames: bool = False,
) -> LprService:
    rotulo_validator = (
        PlateValidator(formats=build_rotulo_formats(), min_length=3, max_length=6)
        if rotulo_engine is not None
        else None
    )
    return LprService(
        camera_service=camera,
        engine=engine,
        storage=LprResultStorage(
            base_path=str(tmp_path / "lpr"),
            public_base_url="http://localhost:8000/evidence",
        ),
        normalizer=PlateNormalizer(),
        validator=PlateValidator(),
        min_confidence=min_confidence,
        catalog=DominicanPlatePatternCatalog() if catalog else None,
        fallback_engine=fallback_engine,
        engine_mode=engine_mode,
        unavailable_engines=unavailable_engines,
        rotulo_engine=rotulo_engine,
        rotulo_validator=rotulo_validator,
        rotulo_min_confidence=rotulo_min_confidence,
        save_debug_evidence=save_debug_evidence,
        burst_frame_count=burst_frame_count,
        burst_interval_ms=burst_interval_ms,
        burst_top_frames=burst_top_frames,
        min_frame_sharpness=min_frame_sharpness,
        min_frame_brightness=min_frame_brightness,
        max_frame_brightness=max_frame_brightness,
        consensus_min_votes=consensus_min_votes,
        single_frame_accept_confidence=single_frame_accept_confidence,
        save_burst_frames=save_burst_frames,
    )
