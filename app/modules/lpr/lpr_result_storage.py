"""Persistencia de evidencia LPR.

Guarda el frame analizado en ``<base>/frames`` y el recorte de placa en
``<base>/crops`` (base típica: ``./evidence/lpr``). Construye la ruta relativa y
la URL pública, siempre con ``/`` (nunca ``\\``) para que sean válidas en la
respuesta JSON y en el navegador/Ignition.

Nunca escribe en ``evidence/snapshots`` (eso es exclusivo de Camera POST
/snapshots). Reutiliza el mismo StaticFiles ``/evidence`` ya montado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StoredEvidence:
    filename: str
    path: str  # relativa, normalizada con "/"
    url: str  # URL pública servida por StaticFiles
    size_bytes: int


class LprResultStorage:
    def __init__(self, base_path: str, public_base_url: str) -> None:
        # `base_path` típico: "./evidence/lpr". Path normaliza el "./".
        self._base_path = Path(base_path)
        self._public_base_url = public_base_url.rstrip("/")

    def save_frame(
        self, frame_bytes: bytes, detected_at: datetime, prefix: str = "lpr_frame"
    ) -> StoredEvidence:
        return self._save("frames", frame_bytes, detected_at, prefix)

    def save_crop(
        self, crop_bytes: bytes, detected_at: datetime, prefix: str = "plate_crop"
    ) -> StoredEvidence:
        return self._save("crops", crop_bytes, detected_at, prefix)

    def save(
        self, subdir: str, data: bytes, detected_at: datetime, prefix: str
    ) -> StoredEvidence:
        """Guarda evidencia arbitraria en ``<base>/<subdir>``.

        Usado para la evidencia de depuración (recortes de ROI, overlay) en
        subdirectorios propios (``roi``, ``overlay``), separados de los
        ``frames``/``crops`` del flujo principal.
        """
        return self._save(subdir, data, detected_at, prefix)

    def _save(
        self, subdir: str, data: bytes, detected_at: datetime, prefix: str
    ) -> StoredEvidence:
        target_dir = self._base_path / subdir
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{prefix}_{detected_at.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        (target_dir / filename).write_bytes(data)

        # `self._base_path.name` es el segmento bajo /evidence (p.ej. "lpr").
        return StoredEvidence(
            filename=filename,
            path=f"{self._base_path.as_posix()}/{subdir}/{filename}",
            url=f"{self._public_base_url}/{self._base_path.name}/{subdir}/{filename}",
            size_bytes=len(data),
        )
