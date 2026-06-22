"""Servicio de negocio sobre Navis: consolida truck-info + driver-info."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.core.errors import NavisError
from app.integrations.navis.navis_client import NavisClient
from app.integrations.navis.navis_models import (
    NavisDriver,
    NavisQueryResult,
    NavisTruck,
)


def _data_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Extrae `response.data` (dict) de un payload de consulta Navis."""
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        return response["data"]
    return {}


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class NavisService:
    def __init__(self, client: NavisClient) -> None:
        self._client = client

    def consultar(
        self, truck: str | None = None, driver: str | None = None
    ) -> NavisQueryResult:
        if not truck and not driver:
            raise NavisError("Debe indicar al menos truck o driver para consultar Navis")

        payloads: list[dict[str, Any]] = []
        truck_model: NavisTruck | None = None
        driver_model: NavisDriver | None = None

        if truck:
            payload = self._client.get_truck_info(truck)
            payloads.append(payload)
            truck_model = self._to_truck(_data_of(payload))

        if driver:
            payload = self._client.get_driver_info(driver)
            payloads.append(payload)
            driver_model = self._to_driver(_data_of(payload))

        all_ok = bool(payloads) and all(p.get("http_status") == 200 for p in payloads)
        success = bool(payloads) and all(
            p.get("http_status") == 200
            and isinstance(p.get("response"), dict)
            and p["response"].get("success") is True
            for p in payloads
        )
        logger.info(
            "Navis: consulta consolidada truck={} driver={} success={}", truck, driver, success
        )
        return NavisQueryResult(
            timestamp=datetime.now(timezone.utc),
            success=success,
            status="OK" if all_ok else "ERROR",
            truck=truck_model,
            driver=driver_model,
            results=payloads,
        )

    @staticmethod
    def _to_truck(data: dict[str, Any]) -> NavisTruck:
        last_trk = data.get("last_trkc", data.get("last_trk", ""))
        return NavisTruck(
            id=_text(data.get("id")),
            license=_text(data.get("license")),
            license_state=_text(data.get("license_state")),
            license_expiration_date=_text(data.get("license_expiration_date")),
            internal_truck=data.get("internal_truck"),
            status=_text(data.get("status")),
            last_trk=_text(last_trk),
            last_truck_driver_name=_text(data.get("last_truck_driver_name")),
            life_cycle_state=_text(data.get("life_cycle_state")),
            raw=data,
        )

    @staticmethod
    def _to_driver(data: dict[str, Any]) -> NavisDriver:
        return NavisDriver(
            name=_text(data.get("name")),
            card_id=_text(data.get("card_id")),
            license=_text(data.get("license")),
            callup_id=_text(data.get("callup_id")),
            license_state=_text(data.get("license_state")),
            status=_text(data.get("status")),
            internal=data.get("internal"),
            life_cycle_state=_text(data.get("life_cycle_state")),
            raw=data,
        )
