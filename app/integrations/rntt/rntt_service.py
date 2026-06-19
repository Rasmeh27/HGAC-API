"""Servicio de consultas RNTT."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from loguru import logger

from app.core.errors import RnttPlateNotFoundError
from app.integrations.rntt.rntt_client import RnttClient
from app.integrations.rntt.rntt_models import RnttPolicy, RnttResult, RnttVehicle


class RnttService:
    def __init__(self, client: RnttClient) -> None:
        self._client = client

    def consultar_placa(self, placa: str) -> RnttResult:
        normalized = placa.strip().upper()
        logger.info("RNTT: consultando placa {}", normalized)

        try:
            raw = self._client.fetch_plate(normalized)
        except RnttPlateNotFoundError:
            return RnttResult(
                plate=normalized,
                status="NOT_FOUND",
                queried_at=datetime.now(timezone.utc),
            )

        return self._to_result(normalized, raw)

    @staticmethod
    def _to_result(plate: str, raw: dict[str, Any]) -> RnttResult:
        vehicle_raw = raw.get("vehicle") or {}
        vehicle = RnttVehicle(
            plate=plate,
            brand=vehicle_raw.get("brand"),
            model=vehicle_raw.get("model"),
            color=vehicle_raw.get("color"),
            year=vehicle_raw.get("year"),
        ) if vehicle_raw else None

        policies = [
            RnttPolicy(
                name=p.get("name", ""),
                expires_at=_parse_date(p.get("expires_at")),
                is_valid=bool(p.get("is_valid", True)),
            )
            for p in raw.get("policies", [])
        ]

        return RnttResult(
            plate=plate,
            status=str(raw.get("status", "ACTIVE")).upper(),
            vehicle=vehicle,
            policies=policies,
            queried_at=datetime.now(timezone.utc),
        )


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
