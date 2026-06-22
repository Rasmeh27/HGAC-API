"""Tests de Wialon (solo mocks, sin red).

Cliente con `requests.Session` falsa para login; servicio con cliente falso para
resumen de unidades; y geometría/geocercas con los helpers puros.
"""

from __future__ import annotations

import time

import pytest

from app.core.errors import WialonAuthenticationError
from app.integrations.wialon import wialon_geo
from app.integrations.wialon.wialon_client import WialonClient
from app.integrations.wialon.wialon_service import WialonService

_TERMINAL = ["TERMINAL GENERAL", "TERMINAL"]
_GATE = ["GATE", "ENTRADA", "SALIDA"]


# ---- cliente: login ----


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload


class _FakeLoginSession:
    def __init__(self, payload) -> None:
        self._payload = payload

    def get(self, url, params=None, timeout=None, **kwargs):
        return _FakeResponse(self._payload)


def test_login_exitoso_setea_sid() -> None:
    client = WialonClient(host="https://hst-api.wialon.com", token="tok")
    client._session = _FakeLoginSession({"eid": "SID123", "au": "demo"})
    client.login()
    assert client._sid == "SID123"


def test_login_fallido_lanza_auth_error() -> None:
    client = WialonClient(host="https://hst-api.wialon.com", token="bad")
    client._session = _FakeLoginSession({"error": 8, "reason": "token inválido"})
    with pytest.raises(WialonAuthenticationError):
        client.login()


# ---- servicio: resumen de unidades ----


class _FakeWialonClient:
    def __init__(self, units, zones=None) -> None:
        self._units = units
        self._zones = zones or []

    def login(self) -> None:
        pass

    def logout(self) -> None:
        pass

    def get_units(self):
        return self._units

    def load_geofences(self):
        return self._zones


def _service(units, zones=None, online_seconds=300) -> WialonService:
    return WialonService(
        client=_FakeWialonClient(units, zones),
        terminal_geofence_names=_TERMINAL,
        gate_zone_keywords=_GATE,
        online_seconds=online_seconds,
    )


def test_unidades_vacias() -> None:
    result = _service([]).get_units_summary()
    assert result.total_units == 0
    assert result.selection_mode == "NO_UNITS"
    assert result.selected_unit is None


def test_unidad_online() -> None:
    now = int(time.time())
    units = [{"id": 1, "nm": "Camion-1", "pos": {"x": -70.0, "y": 18.0, "t": now, "s": 40}}]
    result = _service(units).get_units_summary()
    assert result.total_units == 1
    assert result.selected_unit is not None
    assert result.selected_unit.online is True
    assert result.selected_unit.velocidad == 40.0


def test_unidad_offline_por_gps_viejo() -> None:
    old = int(time.time()) - 1000  # > 300 s
    units = [{"id": 2, "nm": "Camion-2", "pos": {"x": -70.0, "y": 18.0, "t": old}}]
    result = _service(units, online_seconds=300).get_units_summary()
    assert result.selected_unit.online is False
    assert result.selected_unit.gps_age_seconds >= 1000


def test_get_unit_no_encontrada_devuelve_none() -> None:
    units = [{"id": 1, "nm": "Camion-1", "pos": {"x": -70.0, "y": 18.0, "t": int(time.time())}}]
    assert _service(units).get_unit("NO-EXISTE") is None


# ---- geocercas (helpers puros) ----


def _square_zone(name: str) -> dict:
    return {
        "n": name,
        "t": 2,
        "w": 0,
        "b": {"min_x": 0, "min_y": 0, "max_x": 10, "max_y": 10},
        "p": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
    }


def test_clasificacion_geocerca_terminal() -> None:
    zones = [_square_zone("TERMINAL GENERAL")]
    matches, inside_terminal, inside_gate = wialon_geo.classify_geofences(
        zones, 5.0, 5.0, _TERMINAL, _GATE
    )
    assert "TERMINAL GENERAL" in matches
    assert inside_terminal is True
    assert inside_gate is False


def test_clasificacion_geocerca_gate_y_punto_fuera() -> None:
    zones = [_square_zone("GATE 1 ENTRADA")]
    _, _, inside_gate = wialon_geo.classify_geofences(zones, 5.0, 5.0, _TERMINAL, _GATE)
    assert inside_gate is True
    # Punto fuera del bounding box → ninguna geocerca.
    matches, term, gate = wialon_geo.classify_geofences(zones, 50.0, 50.0, _TERMINAL, _GATE)
    assert matches == [] and term is False and gate is False
