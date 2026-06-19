"""Tests del módulo de cámara (/api/v1/cameras).

Para no depender de hardware se inyecta un `CameraService` real cuyo único
doble es el `CameraProvider`: una webcam falsa que entrega un JPEG fijo, tanto en
captura puntual (`capture_frame`) como en sesión persistente (`open_session`).
El almacenamiento de evidencia apunta a un directorio temporal, así los tests
comprueban de forma fehaciente qué endpoint escribe en disco y cuál no:

- `GET /snapshot.jpg`  -> NO persiste evidencia (frame en memoria).
- `GET /stream.mjpg`   -> NO persiste evidencia (preview MJPEG en vivo).
- `POST /snapshots`    -> SÍ persiste evidencia y devuelve metadata.

Los casos de "cámara inexistente" usan el servicio real por defecto, porque el
registro rechaza el id antes de tocar cualquier dispositivo.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import camera_service_provider
from app.core.errors import CameraNotAvailableError
from app.integrations.camera.camera_provider import (
    CameraCaptureSession,
    CameraProvider,
    StreamOptions,
)
from app.main import app
from app.modules.camera.camera_registry import CameraRegistry
from app.modules.camera.camera_service import CameraService
from app.modules.camera.camera_stream_manager import CameraStreamManager
from app.modules.camera.snapshot_storage import SnapshotStorage

client = TestClient(app)

KNOWN_CAMERA = "CAM-P-01"
UNKNOWN_CAMERA = "CAM-NO-EXISTE"

# JPEG real de 640x480 (negro) para que la decodificación de dimensiones funcione.
_SAMPLE_JPEG = cv2.imencode(".jpg", np.zeros((480, 640, 3), dtype=np.uint8))[1].tobytes()


class _FakeSession(CameraCaptureSession):
    """Sesión persistente falsa: entrega siempre el mismo frame."""

    def __init__(self) -> None:
        self.released = False

    def read_jpeg(self) -> bytes | None:
        return _SAMPLE_JPEG

    def release(self) -> None:
        self.released = True


class _FakeProvider(CameraProvider):
    """Webcam falsa siempre disponible; cuenta cuántas veces se abre la sesión."""

    def __init__(self) -> None:
        self.open_session_calls = 0
        self.last_session: _FakeSession | None = None

    def capture_frame(self) -> bytes:
        return _SAMPLE_JPEG

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        self.open_session_calls += 1
        self.last_session = _FakeSession()
        return self.last_session


class _FailingProvider(CameraProvider):
    """Cámara que no abre, ni puntual ni en streaming."""

    def capture_frame(self) -> bytes:
        raise CameraNotAvailableError("cámara no disponible")

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        raise CameraNotAvailableError("cámara no disponible")


# Un chunk MJPEG ya formateado, para tests de enrutado con stream finito.
_MULTIPART_CHUNK = (
    b"--frame\r\n"
    b"Content-Type: image/jpeg\r\n"
    b"Content-Length: " + str(len(_SAMPLE_JPEG)).encode("ascii") + b"\r\n\r\n"
    + _SAMPLE_JPEG
    + b"\r\n"
)


class _StubStreamService:
    """Doble de CameraService cuyo stream es FINITO.

    Permite probar el enrutado de /stream.mjpg con `client.get` sin colgarse:
    el `TestClient` (httpx ASGITransport) almacena el cuerpo completo, así que el
    iterador debe terminar. El comportamiento del stream infinito real se prueba
    aparte consumiendo el iterador del servicio directamente.
    """

    def open_mjpeg_stream(self, camera_id: str):
        return iter([_MULTIPART_CHUNK, _MULTIPART_CHUNK])


@dataclass
class _CameraCtx:
    evidence_dir: Path
    provider: _FakeProvider
    manager: CameraStreamManager
    service: CameraService


def _build_service(
    provider: CameraProvider,
    evidence_path: Path,
    first_frame_timeout: float = 5.0,
) -> tuple[CameraService, CameraStreamManager]:
    factory = lambda config: provider  # noqa: E731 - doble de test conciso
    manager = CameraStreamManager(
        provider_factory=factory,
        # FPS alto: frames rápidos para que los tests no esperen.
        options=StreamOptions(width=640, height=480, fps=120, jpeg_quality=75),
        first_frame_timeout=first_frame_timeout,
    )
    service = CameraService(
        registry=CameraRegistry(),
        storage=SnapshotStorage(
            base_path=str(evidence_path),
            public_base_url="http://localhost:8000/evidence",
        ),
        stream_manager=manager,
        provider_factory=factory,
    )
    return service, manager


@pytest.fixture
def camera_ctx(tmp_path):
    """Inyecta un CameraService real con cámara falsa y almacenamiento en tmp."""
    provider = _FakeProvider()
    evidence_path = tmp_path / "evidence" / "snapshots"
    service, manager = _build_service(provider, evidence_path)
    app.dependency_overrides[camera_service_provider] = lambda: service
    try:
        yield _CameraCtx(evidence_path, provider, manager, service)
    finally:
        manager.shutdown()
        app.dependency_overrides.pop(camera_service_provider, None)


def _jpgs(directory: Path) -> list:
    return sorted(directory.glob("*.jpg")) if directory.exists() else []


def _all_files(directory: Path) -> list:
    return sorted(directory.glob("*")) if directory.exists() else []


# --- Cámara inexistente -> 404 (servicio real por defecto, sin hardware) ---


def test_status_unknown_camera_returns_404() -> None:
    response = client.get(f"/api/v1/cameras/{UNKNOWN_CAMERA}/status")
    assert response.status_code == 404
    # Es el 404 del registro (no un 404 de routing).
    assert UNKNOWN_CAMERA in response.json()["detail"]


def test_snapshot_image_unknown_camera_returns_404() -> None:
    response = client.get(f"/api/v1/cameras/{UNKNOWN_CAMERA}/snapshot.jpg")
    assert response.status_code == 404
    assert UNKNOWN_CAMERA in response.json()["detail"]


def test_create_snapshot_unknown_camera_returns_404() -> None:
    response = client.post(f"/api/v1/cameras/{UNKNOWN_CAMERA}/snapshots", json={})
    assert response.status_code == 404
    assert UNKNOWN_CAMERA in response.json()["detail"]


def test_stream_unknown_camera_returns_404(camera_ctx) -> None:
    response = client.get(f"/api/v1/cameras/{UNKNOWN_CAMERA}/stream.mjpg")
    assert response.status_code == 404
    assert UNKNOWN_CAMERA in response.json()["detail"]


# --- Status ---


def test_status_returns_camera_metadata(camera_ctx) -> None:
    response = client.get(f"/api/v1/cameras/{KNOWN_CAMERA}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["camera_id"] == KNOWN_CAMERA
    assert body["online"] is True
    assert body["status"] == "OK"
    assert body["source_type"] == "usb"
    assert body["width"] == 640
    assert body["height"] == 480


# --- GET /snapshot.jpg: JPEG en memoria, sin persistir ni archivos temporales ---


def test_snapshot_image_returns_jpeg(camera_ctx) -> None:
    response = client.get(f"/api/v1/cameras/{KNOWN_CAMERA}/snapshot.jpg")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == _SAMPLE_JPEG
    # Cabeceras anti-caché para el polling de "Reproducir".
    assert response.headers["cache-control"] == (
        "no-store, no-cache, must-revalidate, max-age=0"
    )
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"


def test_snapshot_image_does_not_persist_evidence(camera_ctx) -> None:
    response = client.get(f"/api/v1/cameras/{KNOWN_CAMERA}/snapshot.jpg")
    assert response.status_code == 200

    # Ni un archivo nuevo de NINGÚN tipo: ni evidencia (.jpg) ni temporal.
    assert _all_files(camera_ctx.evidence_dir) == []


# --- GET /stream.mjpg: enrutado (stream finito vía TestClient) ---


def test_stream_route_returns_200_multipart() -> None:
    """El endpoint responde 200 con el media type MJPEG y cabeceras anti-caché.

    Usa un servicio con stream FINITO porque el TestClient almacena el cuerpo
    completo (no podría consumir un stream infinito sin colgarse).
    """
    app.dependency_overrides[camera_service_provider] = lambda: _StubStreamService()
    try:
        response = client.get(f"/api/v1/cameras/{KNOWN_CAMERA}/stream.mjpg")
        assert response.status_code == 200
        assert "multipart/x-mixed-replace" in response.headers["content-type"]
        assert response.headers["cache-control"] == (
            "no-store, no-cache, must-revalidate, max-age=0"
        )
        assert response.headers["pragma"] == "no-cache"
        # Primer chunk MJPEG bien formado, con los bytes JPEG reales.
        assert b"--frame" in response.content
        assert b"Content-Type: image/jpeg" in response.content
        assert _SAMPLE_JPEG in response.content
    finally:
        app.dependency_overrides.pop(camera_service_provider, None)


# --- GET /stream.mjpg: comportamiento del stream real (iterador directo) ---
# El stream real es infinito; se consume el iterador del servicio sin pasar por
# HTTP, así los tests son deterministas y no dependen del buffering del cliente.


def test_stream_yields_well_formed_multipart_chunks(camera_ctx) -> None:
    stream = camera_ctx.service.open_mjpeg_stream(KNOWN_CAMERA)
    try:
        chunk = next(stream)
    finally:
        stream.close()

    assert chunk.startswith(b"--frame\r\n")
    assert b"Content-Type: image/jpeg" in chunk
    assert _SAMPLE_JPEG in chunk  # los bytes JPEG reales viajan en el stream


def test_stream_opens_camera_once_not_per_frame(camera_ctx) -> None:
    stream = camera_ctx.service.open_mjpeg_stream(KNOWN_CAMERA)
    try:
        frames = [next(stream) for _ in range(3)]
    finally:
        stream.close()

    # Se transmitieron varios frames...
    assert len(frames) == 3
    assert all(b"--frame" in f for f in frames)
    # ...pero la cámara se abrió UNA sola vez (no por cada frame).
    assert camera_ctx.provider.open_session_calls == 1


def test_stream_does_not_persist_evidence(camera_ctx) -> None:
    stream = camera_ctx.service.open_mjpeg_stream(KNOWN_CAMERA)
    try:
        next(stream)
        next(stream)
    finally:
        stream.close()

    # Ningún archivo escrito en evidencia durante el stream.
    assert _all_files(camera_ctx.evidence_dir) == []


def test_stream_releases_camera_on_disconnect(camera_ctx) -> None:
    stream = camera_ctx.service.open_mjpeg_stream(KNOWN_CAMERA)
    next(stream)
    assert camera_ctx.provider.last_session is not None
    assert camera_ctx.provider.last_session.released is False

    # Cerrar el iterador simula la desconexión del cliente.
    stream.close()

    assert camera_ctx.provider.last_session.released is True
    assert camera_ctx.manager.latest_frame(KNOWN_CAMERA) is None


def test_stream_camera_error_returns_503(tmp_path) -> None:
    provider = _FailingProvider()
    evidence_path = tmp_path / "evidence" / "snapshots"
    service, manager = _build_service(
        provider, evidence_path, first_frame_timeout=1.0
    )
    app.dependency_overrides[camera_service_provider] = lambda: service
    try:
        # La cámara no abre -> 503 antes de empezar a transmitir (no cuelga).
        response = client.get(f"/api/v1/cameras/{KNOWN_CAMERA}/stream.mjpg")
        assert response.status_code == 503
        # No se creó evidencia por intentar abrir el stream.
        assert _all_files(evidence_path) == []
    finally:
        manager.shutdown()
        app.dependency_overrides.pop(camera_service_provider, None)


# --- POST /snapshots: SÍ persiste evidencia y devuelve metadata ---


def test_create_snapshot_persists_evidence_and_returns_metadata(camera_ctx) -> None:
    payload = {
        "terminal": "HainaOriental",
        "zone": "Entrada",
        "access": "Gate1",
        "lane": "Lane1",
        "event_id": "MANUAL-001",
        "requested_by": "operator",
    }
    response = client.post(f"/api/v1/cameras/{KNOWN_CAMERA}/snapshots", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["camera_id"] == KNOWN_CAMERA
    assert body["status"] == "CAPTURED"
    assert body["filename"].startswith("snapshot_")
    assert body["filename"].endswith(".jpg")
    assert body["size_bytes"] == len(_SAMPLE_JPEG)
    assert body["width"] == 640
    assert body["height"] == 480
    # Rutas/URL siempre con "/" (compatibilidad con Ignition).
    assert "\\" not in body["path"]
    assert body["path"].endswith(f"snapshots/{body['filename']}")
    assert body["url"] == (
        f"http://localhost:8000/evidence/snapshots/{body['filename']}"
    )

    # Exactamente un archivo de evidencia escrito en disco, con los bytes
    # íntegros del frame capturado (no truncado ni corrupto).
    files = _jpgs(camera_ctx.evidence_dir)
    assert len(files) == 1
    assert files[0].name == body["filename"]
    assert files[0].read_bytes() == _SAMPLE_JPEG
