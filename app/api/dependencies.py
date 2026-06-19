"""Dependencias FastAPI para inyectar servicios en las rutas.

Centralizar las dependencias evita instanciar servicios pesados (cámara,
sesión BioStar) en cada request y permite sustituirlos fácilmente en
tests con `app.dependency_overrides`.
"""

from functools import lru_cache

from app.core.config import Settings, get_settings
from app.integrations.biostar.biostar_factory import build_biostar_service
from app.integrations.biostar.biostar_service import BioStarService
from app.integrations.ignition.ignition_factory import build_ignition_writer
from app.integrations.ignition.ignition_json_writer import IgnitionJsonWriter
from app.integrations.lpr.lpr_factory import build_lpr_service
from app.integrations.lpr.lpr_service import LprService
from app.integrations.rntt.rntt_factory import build_rntt_service
from app.integrations.rntt.rntt_service import RnttService
from app.integrations.camera.camera_provider import StreamOptions
from app.integrations.lpr.lpr_engine import LprEngine
from app.integrations.lpr.opencv_easyocr_lpr_engine import OpenCvEasyOcrLprEngine
from app.integrations.lpr.opencv_plate_detector import OpenCvPlateDetector
from app.modules.camera.camera_registry import CameraRegistry
from app.modules.camera.camera_service import CameraService, build_provider_for_camera
from app.modules.camera.camera_stream_manager import CameraStreamManager
from app.modules.camera.snapshot_storage import SnapshotStorage
from app.modules.crossing.crossing_factory import build_crossing_service
from app.modules.crossing.crossing_service import CrossingService
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService as LprReadService
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import (
    PlateFormat,
    PlateValidator,
    build_plate_formats,
)


def settings_provider() -> Settings:
    return get_settings()


@lru_cache
def _cached_lpr_service() -> LprService:
    return build_lpr_service()


@lru_cache
def _cached_rntt_service() -> RnttService:
    return build_rntt_service()


@lru_cache
def _cached_biostar_service() -> BioStarService:
    return build_biostar_service()


@lru_cache
def _cached_crossing_service() -> CrossingService:
    return build_crossing_service()


@lru_cache
def _cached_ignition_writer() -> IgnitionJsonWriter:
    return build_ignition_writer()


@lru_cache
def _cached_camera_stream_manager() -> CameraStreamManager:
    settings = get_settings()
    return CameraStreamManager(
        provider_factory=build_provider_for_camera,
        options=StreamOptions(
            width=settings.camera_stream_width,
            height=settings.camera_stream_height,
            fps=settings.camera_stream_fps,
            jpeg_quality=settings.camera_stream_jpeg_quality,
        ),
        first_frame_timeout=float(settings.camera_stream_open_timeout_seconds),
    )


@lru_cache
def _cached_camera_service() -> CameraService:
    settings = get_settings()
    return CameraService(
        registry=CameraRegistry(),
        storage=SnapshotStorage(
            base_path=settings.evidence_base_path,
            public_base_url=settings.evidence_public_base_url,
        ),
        stream_manager=_cached_camera_stream_manager(),
    )


def lpr_service_provider() -> LprService:
    return _cached_lpr_service()


def rntt_service_provider() -> RnttService:
    return _cached_rntt_service()


def biostar_service_provider() -> BioStarService:
    return _cached_biostar_service()


def crossing_service_provider() -> CrossingService:
    return _cached_crossing_service()


def ignition_writer_provider() -> IgnitionJsonWriter:
    return _cached_ignition_writer()


def camera_service_provider() -> CameraService:
    return _cached_camera_service()


def _build_lpr_engine(
    settings: Settings, formats: tuple[PlateFormat, ...]
) -> LprEngine:
    """Selecciona el motor LPR. Hoy solo el PoC OpenCV+EasyOCR; el contrato
    `LprEngine` permite añadir otros sin tocar el servicio."""
    if settings.lpr_engine == "opencv_easyocr_poc":
        return OpenCvEasyOcrLprEngine(
            detector=OpenCvPlateDetector(),
            gpu=settings.local_lpr_gpu,
            min_text_length=settings.local_lpr_min_text_length,
            max_text_length=settings.local_lpr_max_text_length,
            expected_formats=tuple(fmt.regex for fmt in formats),
            expected_length=settings.lpr_plate_expected_length,
            mode=settings.lpr_mode,
            min_serial_digits=settings.lpr_min_serial_digits,
            early_stop_confidence=settings.lpr_read_min_confidence,
        )
    raise ValueError(f"LPR engine no soportado: {settings.lpr_engine}")


@lru_cache
def _cached_lpr_read_service() -> LprReadService:
    settings = get_settings()
    formats = build_plate_formats(
        settings.lpr_plate_format_name, settings.lpr_plate_format_regex or None
    )
    return LprReadService(
        camera_service=_cached_camera_service(),
        engine=_build_lpr_engine(settings, formats),
        storage=LprResultStorage(
            base_path=settings.lpr_evidence_base_path,
            public_base_url=settings.evidence_public_base_url,
        ),
        normalizer=PlateNormalizer(),
        validator=PlateValidator(
            formats=formats,
            min_length=settings.local_lpr_min_text_length,
            max_length=settings.local_lpr_max_text_length,
        ),
        min_confidence=settings.lpr_read_min_confidence,
        max_processing_ms=settings.lpr_max_processing_ms,
    )


def lpr_read_service_provider() -> LprReadService:
    return _cached_lpr_read_service()
