"""Registro de cámaras conocidas por el backend.

El registro desacopla el `camera_id` lógico que usa Ignition de la fuente física
concreta (webcam USB o RTSP), de modo que se pueda añadir o reapuntar cámaras
sin tocar las rutas ni el servicio.

Las cámaras pueden definirse en código (`_default_cameras`, hoy solo la webcam
USB CAM-P-01) o cargarse desde un JSON externo (`from_json`,
``config/cameras.json``). El JSON nunca debe contener credenciales: la URL RTSP
real se resuelve por nombre de variable de entorno (`source_env`), y `safe_source`
garantiza que la representación devuelta a Ignition no exponga usuario/clave.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.core.errors import CameraNotFoundError


@dataclass(frozen=True)
class CameraConfig:
    """Definición estática de una cámara.

    `source` es la fuente física real (``USB:0`` o la URL RTSP completa, que para
    RTSP puede llevar credenciales). `device_index` es el índice OpenCV para USB.
    Los campos `roi_*` describen la región de interés para LPR (0 = sin ROI).

    Para exponer la cámara a Ignition/API usar SIEMPRE `safe_source`, que elimina
    credenciales de las URLs RTSP.
    """

    camera_id: str
    camera_name: str
    source_type: str
    source: str
    device_index: int = 0
    roi_x: int = 0
    roi_y: int = 0
    roi_width: int = 0
    roi_height: int = 0

    @property
    def has_lpr_roi(self) -> bool:
        return self.roi_width > 0 and self.roi_height > 0

    @property
    def safe_source(self) -> str:
        """Representación de la fuente sin credenciales (segura para responder)."""
        if self.source_type != "rtsp":
            return self.source
        parsed = urlsplit(self.source)
        if not parsed.hostname:
            return "rtsp:no-configurado"
        port = f":{parsed.port}" if parsed.port else ""
        return urlunsplit(
            (parsed.scheme, f"{parsed.hostname}{port}", parsed.path, "", "")
        )


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
            raise CameraNotFoundError(f"Cámara '{camera_id}' no registrada") from exc

    def all(self) -> list[CameraConfig]:
        return list(self._cameras.values())

    @classmethod
    def from_json(cls, path: str | Path) -> "CameraRegistry":
        """Carga el registro desde un JSON externo.

        Si el archivo no existe, cae a las cámaras por defecto (CAM-P-01), de modo
        que el backend siga arrancando en una PoC sin `config/cameras.json`. La URL
        RTSP nunca se versiona: se resuelve desde la variable de entorno indicada
        en `source_env`.
        """
        registry_path = Path(path)
        if not registry_path.exists():
            return cls()

        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        rows = payload.get("cameras", []) if isinstance(payload, dict) else payload
        cameras: list[CameraConfig] = []
        for row in rows:
            source = str(row.get("source", ""))
            source_env = str(row.get("source_env", "")).strip()
            if source_env:
                source = os.environ.get(source_env, "")
            roi = row.get("lpr_roi") or {}
            cameras.append(
                CameraConfig(
                    camera_id=str(row["camera_id"]),
                    camera_name=str(row.get("camera_name", row["camera_id"])),
                    source_type=str(row.get("source_type", "rtsp")),
                    source=source,
                    device_index=int(row.get("device_index", 0)),
                    roi_x=int(roi.get("x", 0)),
                    roi_y=int(roi.get("y", 0)),
                    roi_width=int(roi.get("width", 0)),
                    roi_height=int(roi.get("height", 0)),
                )
            )
        return cls(cameras=cameras)


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
