"""Servicio de cámara: captura y persistencia de snapshots.

Reutiliza la abstracción `CameraProvider` ya existente (webcam/RTSP) en lugar
de hablar con OpenCV directamente, igual que hace el módulo LPR, pero sin
depender de `LprService`. Así el módulo de cámara no arrastra OCR/LPR.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Callable

import cv2
import numpy as np
from loguru import logger

from app.core.errors import CameraError
from app.integrations.camera.camera_provider import CameraProvider
from app.integrations.camera.rtsp_camera_provider import RtspCameraProvider
from app.integrations.camera.webcam_camera_provider import WebcamCameraProvider
from app.modules.camera.camera_models import (
    CameraStatusResponse,
    SnapshotRequest,
    SnapshotResponse,
)
from app.modules.camera.camera_registry import CameraConfig, CameraRegistry
from app.modules.camera.camera_stream_manager import CameraStreamManager
from app.modules.camera.snapshot_storage import SnapshotStorage

ProviderFactory = Callable[[CameraConfig], CameraProvider]


def build_provider_for_camera(config: CameraConfig) -> CameraProvider:
    """Selecciona el `CameraProvider` adecuado para una cámara concreta.

    Mantiene la lógica de elección junto a las cámaras (no en settings globales),
    de modo que cada cámara del registro pueda tener su propia fuente.
    """
    if config.source_type == "rtsp":
        return RtspCameraProvider(rtsp_url=config.source)
    return WebcamCameraProvider(device_index=config.device_index)


class CameraService:
    def __init__(
        self,
        registry: CameraRegistry,
        storage: SnapshotStorage,
        stream_manager: CameraStreamManager,
        provider_factory: ProviderFactory = build_provider_for_camera,
    ) -> None:
        self._registry = registry
        self._storage = storage
        self._stream_manager = stream_manager
        self._provider_factory = provider_factory

    def ensure_camera_exists(self, camera_id: str) -> None:
        """Valida que el `camera_id` exista. Lanza `CameraNotFoundError` si no."""
        self._registry.get(camera_id)

    def get_status(self, camera_id: str) -> CameraStatusResponse:
        """Reporta el estado de la cámara.

        Lanza `CameraNotFoundError` si el id no existe. Si la cámara existe pero
        no se puede abrir/leer, devuelve un estado con ``online=False`` y el
        error, en vez de propagar la excepción: un endpoint de status debe poder
        responder aunque el hardware esté caído.
        """
        config = self._registry.get(camera_id)

        try:
            frame_bytes = self._grab_frame(config)
            width, height = _decode_dimensions(frame_bytes)
        except CameraError as exc:
            logger.warning("Cámara {} no disponible: {}", camera_id, exc)
            return CameraStatusResponse(
                camera_id=config.camera_id,
                camera_name=config.camera_name,
                source_type=config.source_type,
                source=config.safe_source,
                online=False,
                status="OFFLINE",
                last_frame_at=None,
                width=None,
                height=None,
                fps=None,
                error=str(exc),
            )

        return CameraStatusResponse(
            camera_id=config.camera_id,
            camera_name=config.camera_name,
            source_type=config.source_type,
            source=config.safe_source,
            online=True,
            status="OK",
            last_frame_at=datetime.now(timezone.utc),
            width=width,
            height=height,
            fps=None,
            error=None,
        )

    def capture_current_frame(self, camera_id: str) -> bytes:
        """Captura un frame JPEG en memoria, sin tocar disco.

        Usado por `/snapshot.jpg` (frame puntual/diagnóstico). Propaga
        `CameraError` si la cámara no está disponible.
        """
        config = self._registry.get(camera_id)
        return self._grab_frame(config)

    def open_mjpeg_stream(self, camera_id: str) -> Iterator[bytes]:
        """Devuelve un iterador de chunks MJPEG (`multipart/x-mixed-replace`).

        Función normal (no generador): valida la cámara y abre/espera el primer
        frame de forma síncrona, de modo que `CameraNotFoundError` -> 404 y
        `CameraError` -> 503 se propaguen *antes* de empezar a transmitir.
        No persiste evidencia ni crea archivos temporales.
        """
        config = self._registry.get(camera_id)
        frame_iter = self._stream_manager.open_stream(config)
        return _to_multipart(frame_iter)

    def capture_snapshot(
        self,
        camera_id: str,
        request: SnapshotRequest,
    ) -> SnapshotResponse:
        """Captura un frame y lo guarda como evidencia. Propaga `CameraError`."""
        config = self._registry.get(camera_id)
        frame_bytes = self._grab_frame(config)
        captured_at = datetime.now(timezone.utc)
        width, height = _decode_dimensions(frame_bytes)

        stored = self._storage.save(frame_bytes, captured_at=captured_at)
        logger.info(
            "Snapshot {} guardado en {} ({} bytes) [terminal={} lane={} event_id={}]",
            camera_id,
            stored.path,
            stored.size_bytes,
            request.terminal,
            request.lane,
            request.event_id,
        )

        return SnapshotResponse(
            camera_id=config.camera_id,
            camera_name=config.camera_name,
            source_type=config.source_type,
            status="CAPTURED",
            filename=stored.filename,
            path=stored.path,
            url=stored.url,
            size_bytes=stored.size_bytes,
            captured_at=captured_at,
            width=width,
            height=height,
        )

    def _grab_frame(self, config: CameraConfig) -> bytes:
        """Obtiene un frame JPEG.

        Si hay un stream en vivo activo para esta cámara, reutiliza su último
        frame en memoria; así snapshot/evidencia no abren el dispositivo en
        paralelo (evita aperturas simultáneas de la misma webcam USB). Si no hay
        stream, hace una captura puntual con el proveedor.
        """
        cached = self._stream_manager.latest_frame(config.camera_id)
        if cached is not None:
            return cached
        provider = self._provider_factory(config)
        return provider.capture_frame()


def _to_multipart(frames: Iterator[bytes]) -> Iterator[bytes]:
    """Envuelve cada frame JPEG en una parte `multipart/x-mixed-replace`."""
    try:
        for frame in frames:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n"
                + frame
                + b"\r\n"
            )
    finally:
        # Cierre determinista del iterador de cámara -> libera el cliente/worker
        # en cuanto el cliente se desconecta.
        frames.close()


def _decode_dimensions(frame_bytes: bytes) -> tuple[int | None, int | None]:
    """Obtiene (width, height) decodificando el JPEG. Best-effort."""
    try:
        buffer = np.frombuffer(frame_bytes, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        if image is None:
            return None, None
        height, width = image.shape[:2]
        return int(width), int(height)
    except Exception:  # noqa: BLE001 - las dimensiones son informativas, no críticas
        return None, None
