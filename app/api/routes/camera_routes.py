"""Endpoints REST de cámara para consumo por Ignition.

Contrato formal y estable bajo /api/v1/cameras. Independiente del endpoint
técnico /lpr/debug/snapshot (que se conserva para diagnóstico del módulo LPR).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response, StreamingResponse

from app.api.dependencies import camera_service_provider
from app.core.errors import CameraError, CameraNotFoundError
from app.modules.camera.camera_models import (
    CameraStatusResponse,
    SnapshotRequest,
    SnapshotResponse,
)
from app.modules.camera.camera_service import CameraService

router = APIRouter(prefix="/api/v1/cameras", tags=["Cameras"])


@router.get("/{camera_id}/status", response_model=CameraStatusResponse)
def get_camera_status(
    camera_id: str,
    camera_service: CameraService = Depends(camera_service_provider),
) -> CameraStatusResponse:
    try:
        return camera_service.get_status(camera_id)
    except CameraNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{camera_id}/snapshot.jpg")
def get_camera_snapshot_image(
    camera_id: str,
    camera_service: CameraService = Depends(camera_service_provider),
) -> Response:
    """Devuelve un JPEG del frame actual sin persistir evidencia ni escribir
    archivo temporal.

    Los bytes se devuelven directamente desde memoria (apto para polling de
    ~1 req/s del botón "Reproducir" en Ignition Perspective). Las cabeceras
    desactivan caché para que cada poll muestre el frame más reciente.
    """
    try:
        frame_bytes = camera_service.capture_current_frame(camera_id)
    except CameraNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CameraError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/{camera_id}/stream.mjpg")
def stream_camera_mjpeg(
    camera_id: str,
    camera_service: CameraService = Depends(camera_service_provider),
) -> StreamingResponse:
    """Live preview MJPEG (`multipart/x-mixed-replace`).

    Mantiene la cámara abierta mientras el stream esté activo (no abre/cierra por
    frame) y comparte el mismo worker entre clientes. No persiste evidencia ni
    crea archivos temporales. Pensado para el botón "Reproducir" de Ignition
    Perspective (binding a `Image.props.source` cuando `isPlaying = true`).
    """
    try:
        stream = camera_service.open_mjpeg_stream(camera_id)
    except CameraNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CameraError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc

    return StreamingResponse(
        stream,
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.post(
    "/{camera_id}/snapshots",
    response_model=SnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_camera_snapshot(
    camera_id: str,
    request: SnapshotRequest | None = None,
    camera_service: CameraService = Depends(camera_service_provider),
) -> SnapshotResponse:
    """Captura y guarda un snapshot como evidencia, devolviendo su URL pública."""
    payload = request or SnapshotRequest()
    try:
        return camera_service.capture_snapshot(camera_id, payload)
    except CameraNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CameraError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
