"""Escritor de archivos JSON para Ignition.

Pensado como puente temporal. Cada método escribe un archivo nuevo
(`<event_id>_<tipo>.json`) bajo `IGNITION_JSON_OUTPUT_DIR`. Cuando
Ignition consuma directamente el API REST, este módulo se podrá retirar.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel

from app.core.errors import IgnitionError
from app.integrations.ignition.ignition_models import (
    IgnitionBioStarPayload,
    IgnitionCrossingDecisionPayload,
    IgnitionLprPayload,
    IgnitionRnttPayload,
)


class IgnitionJsonWriter:
    def __init__(
        self,
        output_dir: str | Path,
        lpr_latest_path: str | Path | None = None,
    ) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._lpr_latest_path = (
            Path(lpr_latest_path) if lpr_latest_path else self._output_dir / "hgac_lpr.json"
        )

    def write_lpr_result(self, payload: IgnitionLprPayload) -> Path:
        return self._write(payload, suffix="lpr")

    def write_lpr_latest(self, result: BaseModel) -> Path:
        """Publica la ultima lectura LPR en el contrato consumido por Ignition."""
        raw: dict[str, Any] = result.model_dump(mode="json")
        accepted = raw.get("status") == "PLATE_DETECTED"
        payload = {
            "timestamp": raw.get("detected_at"),
            "trigger": True,
            "status": raw.get("status", "ERROR"),
            "plate": raw.get("plate") or "",
            "plate_normalized": raw.get("plate_normalized") or "",
            "confidence": float(raw.get("confidence") or 0.0),
            "camera_id": raw.get("camera_id") or "",
            "camera_name": raw.get("camera_name") or "",
            "camera_ip": raw.get("camera_ip") or "",
            "frame_path": raw.get("source_frame_path") or "",
            "frame_url": raw.get("source_frame_url") or "",
            "crop_path": raw.get("plate_crop_path") or "",
            "crop_url": raw.get("plate_crop_url") or "",
            "clip_path": "",
            "plate_matched": accepted,
            "rejection_reason": raw.get("rejection_reason") or "",
            "consensus_votes": int(raw.get("consensus_votes") or 0),
            "consensus_total": int(raw.get("consensus_total") or 0),
            "consensus_ratio": float(raw.get("consensus_ratio") or 0.0),
            "event_id": raw.get("event_id") or "",
            "engine": raw.get("engine") or "",
            "raw_result": raw,
        }
        return self._write_atomic_json(self._lpr_latest_path, payload)

    def write_biostar_result(self, payload: IgnitionBioStarPayload) -> Path:
        return self._write(payload, suffix="biostar")

    def write_rntt_result(self, payload: IgnitionRnttPayload) -> Path:
        return self._write(payload, suffix="rntt")

    def write_crossing_decision(self, payload: IgnitionCrossingDecisionPayload) -> Path:
        return self._write(payload, suffix="crossing")

    def _write(self, payload: BaseModel, suffix: str) -> Path:
        event_id = getattr(payload, "event_id", None) or _fallback_event_id()
        filename = f"{event_id}_{suffix}.json"
        path = self._output_dir / filename
        try:
            path.write_text(
                payload.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise IgnitionError(f"No se pudo escribir {path}: {exc}") from exc

        logger.info("Ignition outbox: {}", path)
        return path

    def _write_atomic_json(self, path: Path, payload: dict[str, Any]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise IgnitionError(f"No se pudo escribir {path}: {exc}") from exc
        logger.info("Ignition LPR latest: {}", path)
        return path


def _fallback_event_id() -> str:
    return f"evt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
