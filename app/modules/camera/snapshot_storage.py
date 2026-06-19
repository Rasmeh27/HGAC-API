"""Persistencia de snapshots como evidencia.

Encapsula el guardado del JPEG en disco y la construcción de la ruta relativa
y la URL pública. La misma convención de nombres (timestamp UTC) que ya usaba
`/lpr/debug/snapshot`, pero aislada en una clase reutilizable.

Las rutas devueltas siempre usan ``/`` como separador (nunca ``\\``) para que
sean válidas tanto en la respuesta JSON como en la URL que consume Ignition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class StoredSnapshot:
    """Resultado de persistir un snapshot."""

    filename: str
    path: str  # relativa, normalizada con "/"
    url: str  # URL pública servida por StaticFiles
    size_bytes: int


class SnapshotStorage:
    def __init__(self, base_path: str, public_base_url: str) -> None:
        # `base_path` típico: "./evidence/snapshots". Path normaliza el "./".
        self._base_path = Path(base_path)
        self._public_base_url = public_base_url.rstrip("/")

    def save(
        self,
        frame_bytes: bytes,
        captured_at: datetime,
        prefix: str = "snapshot",
    ) -> StoredSnapshot:
        self._base_path.mkdir(parents=True, exist_ok=True)

        filename = f"{prefix}_{captured_at.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        output_path = self._base_path / filename
        output_path.write_bytes(frame_bytes)

        return StoredSnapshot(
            filename=filename,
            path=f"{self._base_path.as_posix()}/{filename}",
            url=f"{self._public_base_url}/{self._base_path.name}/{filename}",
            size_bytes=len(frame_bytes),
        )
