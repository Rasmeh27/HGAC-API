"""Registro de cámaras conocidas por el backend.

En esta fase de PoC solo existe una cámara (CAM-P-01, webcam USB). El registro
desacopla el `camera_id` lógico que usa Ignition de la fuente física concreta,
de modo que más adelante se pueda añadir o reapuntar cámaras (p.ej. a RTSP) sin
tocar las rutas ni el servicio.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import CameraNotFoundError


@dataclass(frozen=True)
class CameraConfig:
    """Definición estática de una cámara.

    `source` es la representación legible/estable que se devuelve a Ignition
    (p.ej. ``USB:0`` o una URL RTSP saneada). `device_index` es el índice real
    de OpenCV para fuentes USB.
    """

    camera_id: str
    camera_name: str
    source_type: str
    source: str
    device_index: int


class CameraRegistry:
    """Catálogo en memoria de cámaras disponibles."""

    def __init__(self, cameras: list[CameraConfig] | None = None) -> None:
        self._cameras: dict[str, CameraConfig] = {}
        for camera in cameras if cameras is not None else _default_cameras():
            self.register(camera)

    def register(self, camera: CameraConfig) -> None:
        self._cameras[camera.camera_id] = camera

    def get(self, camera_id: str) -> CameraConfig:
        """Devuelve la cámara o lanza `CameraNotFoundError` si no existe."""
        try:
            return self._cameras[camera_id]
        except KeyError as exc:
            raise CameraNotFoundError(
                f"Cámara '{camera_id}' no registrada"
            ) from exc

    def all(self) -> list[CameraConfig]:
        return list(self._cameras.values())


def _default_cameras() -> list[CameraConfig]:
    """Cámaras de la PoC. Hoy solo la webcam USB local."""
    return [
        CameraConfig(
            camera_id="CAM-P-01",
            camera_name="Cámara USB PoC",
            source_type="usb",
            source="USB:0",
            device_index=0,
        ),
    ]
