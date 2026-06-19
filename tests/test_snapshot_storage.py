"""Tests de `SnapshotStorage` (lógica pura de guardado).

Verifica que el archivo se escribe y que ruta/URL se construyen con "/" para
compatibilidad con Ignition, sin depender de cámara real.
"""

from datetime import datetime, timezone

from app.modules.camera.snapshot_storage import SnapshotStorage


def test_save_writes_file_and_builds_forward_slash_paths(tmp_path) -> None:
    base = tmp_path / "evidence" / "snapshots"
    storage = SnapshotStorage(
        base_path=str(base),
        public_base_url="http://localhost:8000/evidence",
    )
    captured_at = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)

    stored = storage.save(b"\xff\xd8\xff\xe0jpeg\xff\xd9", captured_at=captured_at)

    written = base / stored.filename
    assert written.exists()
    assert stored.size_bytes == written.stat().st_size

    assert stored.filename.startswith("snapshot_")
    assert stored.filename.endswith(".jpg")

    # Rutas y URL siempre con "/" (nunca backslash), aun en Windows.
    assert "\\" not in stored.path
    assert stored.path.endswith(f"snapshots/{stored.filename}")
    assert stored.url == (
        f"http://localhost:8000/evidence/snapshots/{stored.filename}"
    )
