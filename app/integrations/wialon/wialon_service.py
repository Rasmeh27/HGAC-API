"""Servicio de negocio sobre Wialon: resumen de unidades + clasificación de geocercas.

El modo "monitor continuo" del script original se porta como una operación
consultable (un tick = una llamada). El monitoreo periódico queda como paso
siguiente (background task), documentado en el README.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.integrations.wialon import wialon_geo
from app.integrations.wialon.wialon_client import WialonClient
from app.integrations.wialon.wialon_models import WialonUnitSummary, WialonUnitsResult


class WialonService:
    def __init__(
        self,
        client: WialonClient,
        terminal_geofence_names: list[str],
        gate_zone_keywords: list[str],
        online_seconds: int = 300,
        geofences_cache_ttl_seconds: int = 60,
    ) -> None:
        self._client = client
        self._terminal_names = terminal_geofence_names
        self._gate_keywords = gate_zone_keywords
        self._online_seconds = online_seconds
        self._geofences_cache_ttl = geofences_cache_ttl_seconds
        self._geofences_cache: list[dict[str, Any]] = []
        self._geofences_expires_at: float = 0.0

    # ---- API pública ----

    def get_units_summary(
        self, target: str = "", online_seconds: Optional[int] = None
    ) -> WialonUnitsResult:
        online_seconds = online_seconds or self._online_seconds
        self._client.login()
        try:
            units = self._client.get_units()
            zones = self._load_geofences()
        finally:
            self._client.logout()

        summaries = [self._unit_summary(u, zones, online_seconds) for u in units]
        selected, mode = self._select_unit(units, target)

        selected_summary: Optional[WialonUnitSummary] = None
        status = "OK"
        if selected:
            selected_id = str(selected.get("id", ""))
            summaries.sort(key=lambda s: 0 if str(s.id) == selected_id else 1)
            selected_summary = summaries[0] if summaries else None
        else:
            status = mode

        return WialonUnitsResult(
            timestamp=datetime.now(timezone.utc),
            status=status,
            selection_mode=mode,
            target_unit=target or "LATEST_GPS",
            total_units=len(units),
            selected_unit=selected_summary,
            unidades=summaries,
        )

    def get_unit(
        self, target: str, online_seconds: Optional[int] = None
    ) -> Optional[WialonUnitSummary]:
        online_seconds = online_seconds or self._online_seconds
        self._client.login()
        try:
            units = self._client.get_units()
            zones = self._load_geofences()
        finally:
            self._client.logout()

        selected, mode = self._select_unit(units, target)
        if not selected:
            logger.info("Wialon: unidad no encontrada para target='{}' ({})", target, mode)
            return None
        return self._unit_summary(selected, zones, online_seconds)

    # ---- internos ----

    def _load_geofences(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._geofences_cache and now < self._geofences_expires_at:
            return self._geofences_cache
        zones = self._client.load_geofences()
        self._geofences_cache = zones
        self._geofences_expires_at = now + self._geofences_cache_ttl
        logger.debug("Wialon: {} geocercas cargadas", len(zones))
        return zones

    def _unit_summary(
        self, unit: dict[str, Any], zones: list[dict[str, Any]], online_seconds: int
    ) -> WialonUnitSummary:
        pos = unit.get("pos", {}) or {}
        report_ts = int(pos.get("t", 0) or 0)
        age_seconds = max(0, int(time.time()) - report_ts) if report_ts else None
        lon = float(pos.get("x", 0) or 0)
        lat = float(pos.get("y", 0) or 0)
        geofences, inside_terminal, inside_gate_zone = wialon_geo.classify_geofences(
            zones, lon, lat, self._terminal_names, self._gate_keywords
        )
        return WialonUnitSummary(
            id=unit.get("id"),
            nombre=unit.get("nm", ""),
            guid=unit.get("gd", ""),
            unique_id=unit.get("uid", ""),
            clase_objeto=unit.get("cls", ""),
            clase_objeto_desc=wialon_geo.class_label(unit.get("cls", "")),
            lat=lat,
            lon=lon,
            velocidad=float(pos.get("s", 0) or 0),
            rumbo=float(pos.get("c", 0) or 0),
            altitud=float(pos.get("z", 0) or 0),
            satelites=int(pos.get("sc", 0) or 0),
            ultimo_reporte=wialon_geo.ts_to_str(report_ts),
            ultimo_reporte_ts=report_ts,
            gps_age_seconds=age_seconds,
            online=bool(report_ts and age_seconds is not None and age_seconds <= online_seconds),
            geofences=geofences,
            geofence_name=", ".join(geofences),
            inside_terminal=inside_terminal,
            inside_gate_zone=inside_gate_zone,
        )

    @staticmethod
    def _select_unit(
        units: list[dict[str, Any]], target: str = ""
    ) -> tuple[Optional[dict[str, Any]], str]:
        if not units:
            return None, "NO_UNITS"

        target = str(target or "").strip().lower()
        if target:
            exact: list[dict[str, Any]] = []
            partial: list[dict[str, Any]] = []
            for unit in units:
                values = [
                    str(unit.get("id", "")).lower(),
                    str(unit.get("nm", "")).lower(),
                    str(unit.get("uid", "")).lower(),
                ]
                if target in values:
                    exact.append(unit)
                elif any(target in value for value in values if value):
                    partial.append(unit)
            matches = exact or partial
            return (matches[0], "TARGET") if matches else (None, "TARGET_NOT_FOUND")

        # Sin objetivo: la unidad con el reporte GPS más reciente.
        selected = max(units, key=lambda u: int((u.get("pos") or {}).get("t", 0) or 0))
        return selected, "LATEST_GPS"
