"""Dependencias FastAPI para inyectar servicios en las rutas.

Centralizar las dependencias evita instanciar servicios pesados (cámara,
sesión BioStar) en cada request y permite sustituirlos fácilmente en
tests con `app.dependency_overrides`.
"""

from functools import lru_cache

from fastapi import HTTPException, status

from app.core.config import Settings, get_settings
from app.core.errors import IntegrationError
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


def _build_lpr_engine(
    settings: Settings,
    formats: tuple[PlateFormat, ...],
    validator: PlateValidator,
    catalog: DominicanPlatePatternCatalog | None,
) -> LprEngine:
    """Selecciona el motor LPR según `LPR_ENGINE`.

    - `opencv_easyocr_poc`: motor propio OpenCV + EasyOCR (por defecto).
    - `simplelpr_rd_poc`: motor alternativo SimpleLPR (dependencia opcional; se
      importa de forma perezosa al construir el motor). Si SimpleLPR no está
      instalado, `SimpleLprEngine` lanza `LprError` (se traduce a 503 en el
      provider). El catálogo dominicano sigue siendo la autoridad de formato.
    - Cualquier otro valor -> error claro.
    """
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
            pad_left_ratio=settings.lpr_pad_left_ratio,
            pad_right_ratio=settings.lpr_pad_right_ratio,
            pad_y_ratio=settings.lpr_pad_y_ratio,
        )
    if settings.lpr_engine == "simplelpr_rd_poc":
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
    raise ValueError(f"LPR engine no soportado: {settings.lpr_engine}")


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
    return LprReadService(
        camera_service=_cached_camera_service(),
        engine=_build_lpr_engine(settings, formats, validator, catalog),
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
