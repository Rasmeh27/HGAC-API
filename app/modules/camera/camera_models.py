"""Modelos request/response del módulo de cámara.

Contrato HTTP que consume Ignition. Se mantiene independiente de los modelos
internos (`CameraConfig`, `CameraProvider`) para que el shape expuesto no
cambie aunque la fuente pase de webcam USB a RTSP.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CameraStatusResponse(BaseModel):
    """Estado actual de una cámara, apto para polling desde Ignition."""

    camera_id: str
    camera_name: str
    source_type: str
    source: str
    online: bool
    status: str
    last_frame_at: datetime | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    error: str | None = None


class SnapshotRequest(BaseModel):
    """Metadatos opcionales del contexto de captura.

    Todos los campos son opcionales: en esta fase solo se registran en el log.
    No se persisten en base de datos ni se publican en colas todavía.
    """

    terminal: str | None = None
    zone: str | None = None
    access: str | None = None
    lane: str | None = None
    event_id: str | None = None
    requested_by: str | None = None


class SnapshotResponse(BaseModel):
    """Resultado de una captura persistida como evidencia."""

    camera_id: str
    camera_name: str
    source_type: str
    status: str
    filename: str
    path: str
    url: str
    size_bytes: int
    captured_at: datetime
    width: int | None = None
    height: int | None = None
