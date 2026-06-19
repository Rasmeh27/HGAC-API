"""Abstracción de proveedor de cámara.

Permite intercambiar la fuente de imagen (webcam local, RTSP, futuro ONVIF)
sin que el resto del backend dependa de OpenCV ni de un protocolo concreto.

Dos modos de uso:

- `capture_frame()`  -> captura puntual (abre/lee/cierra). La usan los endpoints
  de snapshot puntual y de evidencia.
- `open_session(options)` -> captura **persistente**: abre el dispositivo una
  sola vez y permite leer N frames seguidos antes de liberar. La usa el stream
  MJPEG en vivo para no abrir/cerrar la cámara en cada frame.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StreamOptions:
    """Parámetros de una sesión de captura persistente.

    `width`/`height`/`fps` son sugerencias best-effort al dispositivo (no todas
    las fuentes las respetan, p.ej. RTSP). `jpeg_quality` controla la
    codificación de cada frame del stream.
    """

    width: int | None = None
    height: int | None = None
    fps: int | None = None
    jpeg_quality: int = 75


class CameraCaptureSession(ABC):
    """Handle de una cámara abierta. Se lee repetidamente y luego se libera.

    No es thread-safe por sí misma: está pensada para ser usada por un único
    worker de captura (ver `CameraStreamManager`).
    """

    @abstractmethod
    def read_jpeg(self) -> bytes | None:
        """Lee el siguiente frame y lo devuelve como JPEG, o `None` si falla."""

    @abstractmethod
    def release(self) -> None:
        """Libera el dispositivo subyacente."""


class CameraProvider(ABC):
    """Contrato mínimo de cualquier fuente de imagen."""

    @abstractmethod
    def capture_frame(self) -> bytes:
        """Captura un frame y lo devuelve codificado como bytes JPEG.

        Debe lanzar `CameraNotAvailableError` si no se puede abrir el dispositivo
        y `CameraTimeoutError` si la captura tarda más del timeout configurado.
        """

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        """Abre una sesión de captura persistente para streaming.

        Por defecto no se soporta; los proveedores que permitan streaming
        (webcam, RTSP) lo implementan. Debe lanzar `CameraNotAvailableError` si
        no se puede abrir el dispositivo.
        """
        raise NotImplementedError(
            f"{type(self).__name__} no soporta captura persistente (streaming)"
        )
