"""Tests del módulo LPR (POST /api/v1/lpr/reads).

Sin webcam real ni EasyOCR real: se inyecta un `LprService` con motor falso y
una `CameraService` real cuya única pieza falsa es el `CameraProvider`. El
almacenamiento (evidencia LPR y snapshots de cámara) apunta a directorios
temporales, así los tests verifican qué se escribe en disco y qué no.

Semántica de aceptación: `plate`/`plate_normalized` se rellenan SOLO cuando la
lectura es PLATE_DETECTED. Una lectura rechazada (LOW_CONFIDENCE /
FORMAT_MISMATCH) deja `plate` en null y expone el candidato solo en los campos
de depuración (best_raw_text / best_normalized_text).
"""

import re
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import lpr_read_service_provider, settings_provider
from app.core.config import get_settings
from app.core.errors import CameraNotAvailableError
from app.integrations.camera.camera_provider import (
    CameraCaptureSession,
    CameraProvider,
    StreamOptions,
)
from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.main import app
from app.modules.camera.camera_registry import CameraRegistry
from app.modules.camera.camera_service import CameraService
from app.modules.camera.camera_stream_manager import CameraStreamManager
from app.modules.camera.snapshot_storage import SnapshotStorage
from app.modules.lpr.lpr_result_storage import LprResultStorage
from app.modules.lpr.lpr_service import LprService
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateFormat, PlateValidator

TWO_LETTERS_5_DIGITS = (
    PlateFormat(name="TWO_LETTERS_5_DIGITS", regex=r"^[A-Z]{2}[0-9]{5}$"),
)

client = TestClient(app)

KNOWN_CAMERA = "CAM-P-01"
UNKNOWN_CAMERA = "CAM-NO-EXISTE"
READS_URL = "/api/v1/lpr/reads"

_SAMPLE_JPEG = cv2.imencode(".jpg", np.zeros((480, 640, 3), dtype=np.uint8))[1].tobytes()
_CLEAN = re.compile(r"[^A-Z0-9]")


# --- Dobles de cámara ---


class _FakeSession(CameraCaptureSession):
    def read_jpeg(self) -> bytes | None:
        return _SAMPLE_JPEG

    def release(self) -> None:
        pass


class _FakeProvider(CameraProvider):
    def capture_frame(self) -> bytes:
        return _SAMPLE_JPEG

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        return _FakeSession()


class _FailingProvider(CameraProvider):
    def capture_frame(self) -> bytes:
        raise CameraNotAvailableError("cámara no disponible")

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        raise CameraNotAvailableError("cámara no disponible")


# --- Doble del motor LPR ---


class _FakeEngine(LprEngine):
    """Devuelve un candidato fijo + datos de depuración configurables."""

    def __init__(
        self,
        raw_text: str | None,
        confidence: float = 0.0,
        crop: bytes | None = None,
        candidate_count: int = 1,
        ocr_attempt_count: int = 6,
        variant: str = "grayscale",
    ) -> None:
        self._raw = raw_text
        self._confidence = confidence
        self._crop = crop
        self._candidate_count = candidate_count
        self._ocr_attempt_count = ocr_attempt_count
        self._variant = variant

    @property
    def name(self) -> str:
        return "opencv_easyocr_poc"

    def read_plate(self, frame_bgr) -> LprEngineResult:
        normalized = _CLEAN.sub("", self._raw.upper()) if self._raw else None
        return LprEngineResult(
            best_raw_text=self._raw,
            best_normalized_text=normalized,
            confidence=self._confidence,
            plate_crop_jpeg=self._crop,
            candidate_count=self._candidate_count,
            ocr_attempt_count=self._ocr_attempt_count,
            preprocessing_variant=self._variant if self._raw else None,
        )


def _jpgs(directory) -> list:
    return sorted(directory.glob("*.jpg")) if directory.exists() else []


def _all_files(directory) -> list:
    return sorted(directory.glob("*")) if directory.exists() else []


@pytest.fixture
def lpr_env(tmp_path):
    """Devuelve un builder que instala un LprService de test y limpia al final."""
    managers: list[CameraStreamManager] = []

    def _install(
        engine: LprEngine,
        provider: CameraProvider | None = None,
        formats: tuple[PlateFormat, ...] | None = None,
    ):
        provider = provider or _FakeProvider()
        lpr_dir = tmp_path / "evidence" / "lpr"
        snapshots_dir = tmp_path / "evidence" / "snapshots"

        manager = CameraStreamManager(
            provider_factory=lambda config: provider,
            options=StreamOptions(width=640, height=480, fps=120, jpeg_quality=75),
            first_frame_timeout=5.0,
        )
        managers.append(manager)
        camera = CameraService(
            registry=CameraRegistry(),
            storage=SnapshotStorage(
                base_path=str(snapshots_dir),
                public_base_url="http://localhost:8000/evidence",
            ),
            stream_manager=manager,
            provider_factory=lambda config: provider,
        )
        service = LprService(
            camera_service=camera,
            engine=engine,
            storage=LprResultStorage(
                base_path=str(lpr_dir),
                public_base_url="http://localhost:8000/evidence",
            ),
            normalizer=PlateNormalizer(),
            validator=PlateValidator(formats=formats) if formats else PlateValidator(),
            min_confidence=70.0,
            max_processing_ms=5000,
        )
        app.dependency_overrides[lpr_read_service_provider] = lambda: service
        return SimpleNamespace(
            lpr_dir=lpr_dir,
            frames_dir=lpr_dir / "frames",
            crops_dir=lpr_dir / "crops",
            snapshots_dir=snapshots_dir,
        )

    yield _install

    app.dependency_overrides.pop(lpr_read_service_provider, None)
    for manager in managers:
        manager.shutdown()


def _payload(camera_id: str = KNOWN_CAMERA, event_id: str = "LPR-TEST-001") -> dict:
    return {
        "camera_id": camera_id,
        "terminal": "HainaOriental",
        "zone": "Entrada",
        "access": "Gate1",
        "lane": "Lane1",
        "event_id": event_id,
        "requested_by": "operator",
    }


# --- Placa detectada (cumple confianza Y formato) ---


def test_plate_detected_returns_structured_result(lpr_env) -> None:
    # Texto OCR "crudo" con ruido: prueba que el servicio invoca al normalizador
    # (plate conserva lo crudo; plate_normalized es la forma canónica que cumple
    # el formato LETTER_6_DIGITS).
    env = lpr_env(_FakeEngine(raw_text="a-123 456", confidence=87.4, crop=_SAMPLE_JPEG))

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["event_id"] == "LPR-TEST-001"
    assert body["camera_id"] == KNOWN_CAMERA
    assert body["status"] == "PLATE_DETECTED"
    assert body["plate"] == "a-123 456"
    assert body["plate_normalized"] == "A123456"
    assert body["confidence"] == 87.4
    assert body["engine"] == "opencv_easyocr_poc"
    assert body["processing_time_ms"] >= 0

    # Campos de depuración.
    assert body["format_valid"] is True
    assert body["rejection_reason"] is None
    assert body["best_raw_text"] == "a-123 456"
    assert body["best_normalized_text"] == "A123456"
    assert body["expected_format"] == "LETTER_6_DIGITS"
    assert body["candidate_count"] == 1
    assert body["ocr_attempt_count"] == 6
    assert body["preprocessing_variant"] == "grayscale"
    assert body["crop_saved"] is True

    # URL pública (usa el segmento "lpr", estable) y ruta normalizada con "/".
    frame_name = body["source_frame_url"].rsplit("/", 1)[-1]
    crop_name = body["plate_crop_url"].rsplit("/", 1)[-1]
    assert body["source_frame_url"].startswith(
        "http://localhost:8000/evidence/lpr/frames/"
    )
    assert body["plate_crop_url"].startswith(
        "http://localhost:8000/evidence/lpr/crops/"
    )
    assert body["source_frame_path"].endswith(f"lpr/frames/{frame_name}")
    assert body["plate_crop_path"].endswith(f"lpr/crops/{crop_name}")
    assert "\\" not in body["source_frame_path"]
    assert "\\" not in body["plate_crop_path"]

    # Evidencia: frame + crop guardados; nada en snapshots.
    assert len(_jpgs(env.frames_dir)) == 1
    assert len(_jpgs(env.crops_dir)) == 1
    assert _all_files(env.snapshots_dir) == []


# --- Formato TWO_LETTERS_5_DIGITS (placa tipo OF00105) ---


def test_two_letters_format_accepts_serial(lpr_env) -> None:
    lpr_env(
        _FakeEngine(raw_text="OF00105", confidence=88.0, crop=_SAMPLE_JPEG),
        formats=TWO_LETTERS_5_DIGITS,
    )
    body = client.post(READS_URL, json=_payload()).json()
    assert body["status"] == "PLATE_DETECTED"
    assert body["plate"] == "OF00105"
    assert body["plate_normalized"] == "OF00105"
    assert body["format_valid"] is True
    assert body["rejection_reason"] is None
    assert body["expected_format"] == "TWO_LETTERS_5_DIGITS"


def test_two_letters_format_rejects_header_text(lpr_env) -> None:
    # Si (por el motivo que sea) el candidato fuese "DOMIN", el servicio lo
    # rechaza por formato: nunca se acepta como placa.
    lpr_env(
        _FakeEngine(raw_text="DOMIN", confidence=98.2, crop=_SAMPLE_JPEG),
        formats=TWO_LETTERS_5_DIGITS,
    )
    body = client.post(READS_URL, json=_payload()).json()
    assert body["status"] == "FORMAT_MISMATCH"
    assert body["plate"] is None
    assert body["plate_normalized"] is None
    assert body["best_raw_text"] == "DOMIN"  # visible solo como candidato/debug
    assert body["format_valid"] is False


# --- Lectura incompleta: buena confianza, formato inválido -> FORMAT_MISMATCH ---
# (el bug reportado: "460432" conf 73.3 NO debe pasar como PLATE_DETECTED)


def test_incomplete_plate_is_format_mismatch_not_detected(lpr_env) -> None:
    env = lpr_env(_FakeEngine(raw_text="460432", confidence=73.3, crop=_SAMPLE_JPEG))

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["status"] == "FORMAT_MISMATCH"
    # No se acepta como placa: plate/plate_normalized en null...
    assert body["plate"] is None
    assert body["plate_normalized"] is None
    # ...pero el candidato queda visible para depuración (sin inferir la "L").
    assert body["best_raw_text"] == "460432"
    assert body["best_normalized_text"] == "460432"
    assert body["confidence"] == 73.3
    assert body["format_valid"] is False
    assert body["expected_format"] == "LETTER_6_DIGITS"
    assert body["rejection_reason"] == "format_mismatch"
    # Hubo candidato -> el crop usado para OCR se guarda.
    assert body["crop_saved"] is True
    assert len(_jpgs(env.crops_dir)) == 1
    assert _all_files(env.snapshots_dir) == []


# --- Placa completa que cumple formato -> PLATE_DETECTED ---


def test_complete_plate_passes_format_and_is_detected(lpr_env) -> None:
    env = lpr_env(_FakeEngine(raw_text="L460432", confidence=88.0, crop=_SAMPLE_JPEG))

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["status"] == "PLATE_DETECTED"
    assert body["plate"] == "L460432"
    assert body["plate_normalized"] == "L460432"
    assert body["confidence"] == 88.0
    assert body["format_valid"] is True
    assert body["rejection_reason"] is None
    assert body["crop_saved"] is True
    assert len(_jpgs(env.frames_dir)) == 1
    assert len(_jpgs(env.crops_dir)) == 1
    assert _all_files(env.snapshots_dir) == []


# --- Confianza baja: hay candidato (formato válido) pero por debajo del umbral ---


def test_low_confidence_does_not_accept_plate(lpr_env) -> None:
    # "A123456" cumple formato, pero confianza 40 < umbral 70 -> LOW_CONFIDENCE.
    env = lpr_env(_FakeEngine(raw_text="A123456", confidence=40.0, crop=_SAMPLE_JPEG))

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["status"] == "LOW_CONFIDENCE"
    # No se acepta: plate en null; el candidato solo en depuración.
    assert body["plate"] is None
    assert body["plate_normalized"] is None
    assert body["best_raw_text"] == "A123456"
    assert body["best_normalized_text"] == "A123456"
    assert body["confidence"] == 40.0
    assert body["format_valid"] is True  # el formato sí es válido; falla la confianza
    assert body["rejection_reason"] == "low_confidence"
    # Hubo candidato -> crop guardado.
    assert body["crop_saved"] is True
    assert len(_jpgs(env.crops_dir)) == 1


def test_low_confidence_takes_precedence_over_format_mismatch(lpr_env) -> None:
    # Candidato con confianza baja Y formato inválido ("460432"@40): la confianza
    # se evalúa primero, así que el estado es LOW_CONFIDENCE (no FORMAT_MISMATCH).
    lpr_env(_FakeEngine(raw_text="460432", confidence=40.0, crop=_SAMPLE_JPEG))

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["status"] == "LOW_CONFIDENCE"
    assert body["rejection_reason"] == "low_confidence"
    assert body["format_valid"] is False  # el formato sí es inválido...
    assert body["plate"] is None  # ...pero no se acepta de ningún modo


# --- Sin placa ---


def test_no_plate_detected_returns_nulls_and_no_crop(lpr_env) -> None:
    # El motor devuelve confianza != 0 y BYTES de crop a propósito: el servicio
    # debe FORZAR confidence=0 y NO guardar crop cuando no hay texto.
    env = lpr_env(
        _FakeEngine(
            raw_text=None,
            confidence=55.0,
            crop=_SAMPLE_JPEG,
            candidate_count=2,
            ocr_attempt_count=12,
        )
    )

    response = client.post(READS_URL, json=_payload())
    assert response.status_code in (200, 201)

    body = response.json()
    assert body["status"] == "NO_PLATE_DETECTED"
    assert body["plate"] is None
    assert body["plate_normalized"] is None
    assert body["best_raw_text"] is None
    assert body["confidence"] == 0  # forzado por el servicio, no el 55.0 del motor
    assert body["rejection_reason"] == "no_text"
    assert body["crop_saved"] is False
    # Datos de depuración del motor pasan a través aunque no haya placa.
    assert body["candidate_count"] == 2
    assert body["ocr_attempt_count"] == 12
    assert body["source_frame_url"].startswith(
        "http://localhost:8000/evidence/lpr/frames/"
    )
    assert body["plate_crop_path"] is None
    assert body["plate_crop_url"] is None

    # Frame guardado; crop suprimido pese a que el motor entregó bytes; nada en snapshots.
    assert len(_jpgs(env.frames_dir)) == 1
    assert _jpgs(env.crops_dir) == []
    assert _all_files(env.snapshots_dir) == []


# --- Errores de cámara ---


def test_unknown_camera_returns_404(lpr_env) -> None:
    lpr_env(_FakeEngine(raw_text=None))
    response = client.post(READS_URL, json=_payload(camera_id=UNKNOWN_CAMERA))
    assert response.status_code == 404


def test_camera_error_returns_503(lpr_env) -> None:
    env = lpr_env(_FakeEngine(raw_text=None), provider=_FailingProvider())
    response = client.post(READS_URL, json=_payload())
    assert response.status_code == 503
    assert "did not return a valid frame" in response.json()["detail"]
    # La cámara falló antes de guardar: no se creó evidencia.
    assert _jpgs(env.frames_dir) == []
    assert _all_files(env.snapshots_dir) == []


def test_event_id_is_generated_when_missing(lpr_env) -> None:
    lpr_env(_FakeEngine(raw_text=None))
    response = client.post(READS_URL, json={"camera_id": KNOWN_CAMERA})
    assert response.status_code in (200, 201)
    body = response.json()
    assert body["event_id"].startswith("LPR-")


def test_disabled_module_returns_503(lpr_env) -> None:
    lpr_env(_FakeEngine(raw_text="L460432", confidence=88.0, crop=_SAMPLE_JPEG))
    disabled = get_settings().model_copy(update={"lpr_enabled": False})
    app.dependency_overrides[settings_provider] = lambda: disabled
    try:
        response = client.post(READS_URL, json=_payload())
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(settings_provider, None)
