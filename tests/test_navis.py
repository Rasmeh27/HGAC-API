"""Tests de Navis (solo mocks, sin red).

El cliente se prueba con una `requests.Session` falsa inyectada; el servicio se
prueba sobre ese cliente. Casos del handoff: token exitoso/fallido, truck-info,
driver-info y error HTTP.
"""

from __future__ import annotations

import pytest

from app.core.errors import NavisAuthenticationError
from app.integrations.navis.navis_client import NavisClient
from app.integrations.navis.navis_service import NavisService


class _FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeSession:
    """Session falsa: respuestas programables para POST (token) y GET (consulta)."""

    def __init__(self, token_response: _FakeResponse, get_responses=None) -> None:
        self._token_response = token_response
        self._get_responses = list(get_responses or [])
        self.token_calls = 0
        self.get_calls: list[str] = []

    def post(self, url, data=None, timeout=None, **kwargs):
        self.token_calls += 1
        return self._token_response

    def get(self, url, headers=None, timeout=None, **kwargs):
        self.get_calls.append(url)
        return self._get_responses.pop(0)


def _client(session: _FakeSession) -> NavisClient:
    client = NavisClient(
        api_base="https://qa.example.com",
        username="user",
        password="secret",
        client_id="cid",
        client_secret="csec",
    )
    client._session = session  # inyección del doble
    return client


def _ok_token() -> _FakeResponse:
    return _FakeResponse(200, {"access_token": "abc123", "expires_in": 3600})


# ---- token ----


def test_token_exitoso_y_truck_info() -> None:
    session = _FakeSession(
        token_response=_ok_token(),
        get_responses=[
            _FakeResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "id": "T1",
                        "license": "L312278",
                        "status": "OK",
                        "life_cycle_state": "ACTIVE",
                        "last_trkc": "TRK-9",
                    },
                },
            )
        ],
    )
    service = NavisService(client=_client(session))
    result = service.consultar(truck="AGML16")

    assert result.success is True
    assert result.status == "OK"
    assert result.truck is not None
    assert result.truck.id == "T1"
    assert result.truck.license == "L312278"
    assert result.truck.last_trk == "TRK-9"
    # El token se pidió una sola vez (cacheado para la consulta).
    assert session.token_calls == 1


def test_token_fallido_lanza_auth_error() -> None:
    session = _FakeSession(token_response=_FakeResponse(401, text="invalid_client"))
    client = _client(session)
    with pytest.raises(NavisAuthenticationError):
        client.get_truck_info("AGML16")


# ---- driver-info ----


def test_driver_info_exitoso() -> None:
    session = _FakeSession(
        token_response=_ok_token(),
        get_responses=[
            _FakeResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "name": "Juan Perez",
                        "card_id": "CARD-1",
                        "license": "05100147353",
                        "life_cycle_state": "ACTIVE",
                    },
                },
            )
        ],
    )
    service = NavisService(client=_client(session))
    result = service.consultar(driver="2089")
    assert result.driver is not None
    assert result.driver.name == "Juan Perez"
    assert result.driver.card_id == "CARD-1"
    assert result.success is True


# ---- error HTTP ----


def test_http_error_devuelve_status_error() -> None:
    session = _FakeSession(
        token_response=_ok_token(),
        get_responses=[_FakeResponse(500, text="server error")],
    )
    service = NavisService(client=_client(session))
    result = service.consultar(truck="AGML16")
    assert result.success is False
    assert result.status == "ERROR"
    assert result.results[0]["http_status"] == 500
