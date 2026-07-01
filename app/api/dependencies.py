"""Dependencias FastAPI para inyectar servicios en las rutas.

Centralizar las dependencias evita instanciar servicios pesados (cámara,
sesión BioStar) en cada request y permite sustituirlos fácilmente en
tests con `app.dependency_overrides`.
"""

from functools import lru_cache

from fastapi import HTTPException, status
from loguru import logger

from app.core.config import Settings, get_settings
from app.core.errors import IntegrationError, LprError
from app.integrations.biostar.biostar_factory import build_biostar_service
from app.integrations.biostar.biostar_service import BioStarService
from app.integrations.ignition.ignition_factory import build_ignition_writer
from app.integrations.ignition.ignition_json_writer import IgnitionJsonWriter
from app.integrations.lpr.lpr_factory import build_lpr_service
from app.integrations.lpr.lpr_service import LprService
from app.integrations.navis.navis_factory import build_navis_service
from app.integrations.navis.navis_service import NavisService
from app.integrations.rntt.rntt_factory import build_rntt_asmx_service, build_rntt_service
from app.integrations.rntt.rntt_asmx_service import RnttAsmxService
from app.integrations.rntt.rntt_service import RnttService
from app.integrations.wialon.wialon_factory import build_wialon_service
from app.integrations.wialon.wialon_service import WialonService
from app.integrations.camera.camera_provider import StreamOptions
from app.integrations.lpr.lpr_engine import LprEngine
from app.integrations.lpr.opencv_easyocr_lpr_engine import OpenCvEasyOcrLprEngine
from app.integrations.lpr.opencv_plate_detector import OpenCvPlateDetector
from app.integrations.lpr.simple_lpr_engine import SimpleLprConfig, SimpleLprEngine
from app.modules.camera.camera_registry import CameraRegistry
from app.modules.camera.camera_service import CameraService, build_provider_for_camera
from app.modules.camera.camera_stream_manager import CameraStreamManager
from app.modules.camera.snapshot_storage import SnapshotStorage
from app.modules.crossing.crossing_factory import build_crossing_service
from app.modules.crossing.crossing_service import CrossingService
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService as LprReadService
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import (
    PlateFormat,
    PlateValidator,
    build_plate_formats,
    build_rotulo_formats,
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
def _cached_rntt_asmx_service() -> RnttAsmxService:
    return build_rntt_asmx_service()


@lru_cache
def _cached_navis_service() -> NavisService:
    return build_navis_service()


@lru_cache
def _cached_wialon_service() -> WialonService:
    return build_wialon_service()


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
        registry=CameraRegistry.from_json(settings.camera_registry_path),
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


def _build_or_503(builder, system: str):
    """Construye un servicio de integración; si falta configuración → HTTP 503.

    Los constructores de cliente validan credenciales/host y lanzan
    ``IntegrationError`` cuando faltan; lo traducimos a 503 (no configurado) en
    lugar de 502, sin tocar la red.
    """
    try:
        return builder()
    except IntegrationError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"{system} no configurado: {exc}"
        ) from exc


def rntt_asmx_service_provider() -> RnttAsmxService:
    return _build_or_503(_cached_rntt_asmx_service, "RNTT")


def navis_service_provider() -> NavisService:
    return _build_or_503(_cached_navis_service, "Navis")


def wialon_service_provider() -> WialonService:
    return _build_or_503(_cached_wialon_service, "Wialon")


def crossing_service_provider() -> CrossingService:
    return _cached_crossing_service()


def ignition_writer_provider() -> IgnitionJsonWriter:
    return _cached_ignition_writer()


def camera_service_provider() -> CameraService:
    return _cached_camera_service()


# Alias amigables (LPR_ENGINE / LPR_FALLBACK_ENGINE) -> nombre canónico interno.
_ENGINE_ALIASES = {
    "opencv": "opencv_easyocr_poc",
    "opencv_easyocr": "opencv_easyocr_poc",
    "opencv_easyocr_poc": "opencv_easyocr_poc",
    "easyocr": "opencv_easyocr_poc",
    "simplelpr": "simplelpr_rd_poc",
    "simple_lpr": "simplelpr_rd_poc",
    "simplelpr_rd_poc": "simplelpr_rd_poc",
    "auto": "auto",
}


def _resolve_engine_alias(name: str) -> str:
    token = (name or "").strip().lower()
    return _ENGINE_ALIASES.get(token, token)


def _make_opencv_engine(
    settings: Settings,
    *,
    expected_formats: tuple[str, ...],
    expected_length: int,
    min_text_length: int,
    max_text_length: int,
    min_serial_digits: int,
) -> LprEngine:
    return OpenCvEasyOcrLprEngine(
        detector=OpenCvPlateDetector(),
        gpu=settings.local_lpr_gpu,
        min_text_length=min_text_length,
        max_text_length=max_text_length,
        expected_formats=expected_formats,
        expected_length=expected_length,
        upscale=settings.lpr_ocr_upscale,
        mode=settings.lpr_mode,
        min_serial_digits=min_serial_digits,
        early_stop_confidence=settings.lpr_read_min_confidence,
        pad_x_ratio=max(settings.lpr_pad_left_ratio, settings.lpr_pad_right_ratio),
        pad_y_ratio=settings.lpr_pad_y_ratio,
    )


def _make_simplelpr_engine(
    settings: Settings,
    validator: PlateValidator,
    catalog: DominicanPlatePatternCatalog | None,
) -> LprEngine:
    """Construye SimpleLPR. Puede lanzar `LprError` (no instalado / país inválido)."""
    countries = tuple(
        token.strip()
        for token in settings.simple_lpr_countries.split(",")
        if token.strip()
    )
    return SimpleLprEngine(
        config=SimpleLprConfig(
            countries=countries,
            product_key_path=settings.simple_lpr_product_key_path,
            min_confidence=settings.simple_lpr_min_confidence,
            use_gpu=settings.simple_lpr_use_gpu,
            cuda_device_id=settings.simple_lpr_cuda_device_id,
            max_concurrent_ops=settings.simple_lpr_max_concurrent_ops,
            plate_region_detection=settings.simple_lpr_plate_region_detection,
            crop_to_plate_region=settings.simple_lpr_crop_to_plate_region,
            max_substitutions=settings.simple_lpr_max_ocr_substitutions,
            substitution_penalty=settings.simple_lpr_substitution_penalty,
        ),
        catalog=catalog,
        validator=validator,
    )


def _build_plate_engine_by_name(
    canonical: str,
    settings: Settings,
    plate_formats: tuple[PlateFormat, ...],
    validator: PlateValidator,
    catalog: DominicanPlatePatternCatalog | None,
) -> LprEngine:
    if canonical == "opencv_easyocr_poc":
        return _make_opencv_engine(
            settings,
            expected_formats=tuple(fmt.regex for fmt in plate_formats),
            expected_length=settings.lpr_plate_expected_length,
            min_text_length=settings.local_lpr_min_text_length,
            max_text_length=settings.local_lpr_max_text_length,
            min_serial_digits=settings.lpr_min_serial_digits,
        )
    if canonical == "simplelpr_rd_poc":
        return _make_simplelpr_engine(settings, validator, catalog)
    raise ValueError(f"LPR engine no soportado: {settings.lpr_engine}")


def _build_plate_engines(
    settings: Settings,
    plate_formats: tuple[PlateFormat, ...],
    validator: PlateValidator,
    catalog: DominicanPlatePatternCatalog | None,
) -> tuple[LprEngine, LprEngine | None, list[dict], str]:
    """Resuelve `LPR_ENGINE` (+ fallback) y devuelve (primario, fallback, notas, etiqueta).

    - `opencv_easyocr`: motor propio (por defecto). Sin fallback.
    - `simplelpr`: SimpleLPR; si no se puede construir y hay `LPR_FALLBACK_ENGINE`,
      degrada al fallback (sin romper). Sin fallback -> `LprError` (503 controlado).
    - `auto`: intenta SimpleLPR como primario y OpenCV como fallback; si SimpleLPR
      no está disponible, usa OpenCV como primario. Nunca rompe.

    `notas` son intentos UNAVAILABLE (motor que se quiso usar pero no se pudo
    construir), que el servicio expone en `engine_attempts`.
    """
    mode = _resolve_engine_alias(settings.lpr_engine)
    if mode not in ("auto", "simplelpr_rd_poc", "opencv_easyocr_poc"):
        raise ValueError(f"LPR engine no soportado: {settings.lpr_engine}")
    fallback_name = (
        _resolve_engine_alias(settings.lpr_fallback_engine)
        if settings.lpr_fallback_engine.strip()
        else ""
    )
    unavailable: list[dict] = []

    def build(name: str) -> LprEngine:
        return _build_plate_engine_by_name(name, settings, plate_formats, validator, catalog)

    if mode == "auto":
        try:
            primary = _make_simplelpr_engine(settings, validator, catalog)
            return primary, build("opencv_easyocr_poc"), unavailable, "auto"
        except LprError as exc:
            logger.warning("LPR auto: SimpleLPR no disponible ({}); usando OpenCV.", exc)
            unavailable.append(
                {"engine": "simplelpr_rd_poc", "status": "UNAVAILABLE", "error": str(exc)}
            )
            return build("opencv_easyocr_poc"), None, unavailable, "auto"

    if mode == "simplelpr_rd_poc":
        try:
            primary = _make_simplelpr_engine(settings, validator, catalog)
        except LprError as exc:
            if fallback_name and fallback_name != "simplelpr_rd_poc":
                logger.warning(
                    "LPR: SimpleLPR no disponible ({}); usando fallback {}.",
                    exc,
                    fallback_name,
                )
                unavailable.append(
                    {"engine": "simplelpr_rd_poc", "status": "UNAVAILABLE", "error": str(exc)}
                )
                return build(fallback_name), None, unavailable, "simplelpr_rd_poc"
            raise  # sin fallback configurado -> 503 controlado en el provider
        fallback = (
            build(fallback_name)
            if fallback_name and fallback_name != "simplelpr_rd_poc"
            else None
        )
        return primary, fallback, unavailable, "simplelpr_rd_poc"

    # opencv_easyocr_poc (por defecto). Sin fallback (es el motor confiable).
    return build("opencv_easyocr_poc"), None, unavailable, "opencv_easyocr_poc"


def _build_rotulo_engine_and_validator(
    settings: Settings,
) -> tuple[LprEngine | None, PlateValidator | None]:
    """Motor + validador de RÓTULO (OpenCV/EasyOCR, formatos cortos propios).

    Devuelve (None, None) si la lectura de rótulo está deshabilitada. El motor
    carga EasyOCR de forma perezosa: solo pesa cuando una cámara con `rotulo_roi`
    dispara una lectura.
    """
    if not settings.lpr_rotulo_enabled:
        return None, None
    rotulo_formats = build_rotulo_formats(settings.lpr_rotulo_format_name)
    validator = PlateValidator(
        formats=rotulo_formats,
        min_length=settings.lpr_rotulo_min_text_length,
        max_length=settings.lpr_rotulo_max_text_length,
    )
    engine = _make_opencv_engine(
        settings,
        expected_formats=tuple(fmt.regex for fmt in rotulo_formats),
        expected_length=settings.lpr_rotulo_max_text_length,
        min_text_length=settings.lpr_rotulo_min_text_length,
        max_text_length=settings.lpr_rotulo_max_text_length,
        min_serial_digits=settings.lpr_rotulo_min_serial_digits,
    )
    return engine, validator


@lru_cache
def _cached_lpr_read_service() -> LprReadService:
    settings = get_settings()
    formats = build_plate_formats(
        settings.lpr_plate_format_name, settings.lpr_plate_format_regex or None
    )
    catalog = (
        DominicanPlatePatternCatalog()
        if settings.lpr_enable_dominican_plate_catalog
        else None
    )
    validator = PlateValidator(
        formats=formats,
        min_length=settings.local_lpr_min_text_length,
        max_length=settings.local_lpr_max_text_length,
    )
    primary_engine, fallback_engine, unavailable, engine_mode = _build_plate_engines(
        settings, formats, validator, catalog
    )
    rotulo_engine, rotulo_validator = _build_rotulo_engine_and_validator(settings)
    return LprReadService(
        camera_service=_cached_camera_service(),
        engine=primary_engine,
        storage=LprResultStorage(
            base_path=settings.lpr_evidence_base_path,
            public_base_url=settings.evidence_public_base_url,
        ),
        normalizer=PlateNormalizer(),
        validator=validator,
        min_confidence=settings.lpr_read_min_confidence,
        max_processing_ms=settings.lpr_max_processing_ms,
        catalog=catalog,
        ambiguous_min_score_delta=settings.lpr_ambiguous_min_score_delta,
        ambiguous_candidate_distance=settings.lpr_ambiguous_candidate_distance,
        require_multiframe_confirmation=settings.lpr_require_multiframe_confirmation,
        # Multi-motor (auto/fallback).
        fallback_engine=fallback_engine,
        engine_mode=engine_mode,
        unavailable_engines=unavailable,
        # Rótulo de camión (lectura independiente sobre rotulo_roi).
        rotulo_engine=rotulo_engine,
        rotulo_validator=rotulo_validator,
        rotulo_min_confidence=settings.lpr_rotulo_read_min_confidence,
        # Evidencia de depuración (recortes de ROI + overlay) para calibrar.
        save_debug_evidence=settings.lpr_save_debug_evidence,
        evidence_jpeg_quality=settings.lpr_evidence_jpeg_quality,
        # Ráfaga multiframe + consenso (placas en movimiento).
        burst_frame_count=settings.lpr_burst_frame_count,
        burst_interval_ms=settings.lpr_burst_interval_ms,
        burst_top_frames=settings.lpr_burst_top_frames,
        min_frame_sharpness=settings.lpr_min_frame_sharpness,
        min_frame_brightness=settings.lpr_min_frame_brightness,
        max_frame_brightness=settings.lpr_max_frame_brightness,
        consensus_min_votes=settings.lpr_consensus_min_votes,
        single_frame_accept_confidence=settings.lpr_single_frame_accept_confidence,
        save_burst_frames=settings.lpr_save_burst_frames,
        # Publica cada lectura del endpoint formal en el "latest" de Ignition
        # (escritura atómica). Si el archivo está bloqueado, el observador lo
        # registra sin romper la respuesta HTTP.
        result_observer=_cached_ignition_writer().write_lpr_latest,
    )


def lpr_read_service_provider() -> LprReadService:
    # Si el motor seleccionado es SimpleLPR y el paquete no está instalado (o un
    # país es inválido), `SimpleLprEngine` lanza `LprError` (IntegrationError);
    # lo traducimos a 503 con mensaje claro en vez de un 500 opaco.
    return _build_or_503(_cached_lpr_read_service, "LPR")
