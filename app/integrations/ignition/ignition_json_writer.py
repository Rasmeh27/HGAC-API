"""Escritor de archivos JSON para Ignition.

Pensado como puente temporal. Cada método escribe un archivo nuevo
(`<event_id>_<tipo>.json`) bajo `IGNITION_JSON_OUTPUT_DIR`. Cuando
Ignition consuma directamente el API REST, este módulo se podrá retirar.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def write_lpr_result(self, payload: IgnitionLprPayload) -> Path:
        return self._write(payload, suffix="lpr")

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


def _fallback_event_id() -> str:
    return f"evt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
