"""Tests de eventos/validación BioStar (solo mocks, sin red).

Se inyecta un cliente BioStar falso (login no-op, padrón y dispositivos en
memoria) en `BioStarService`. Casos del handoff: login/dispositivos, evento
permitido, denegado, usuario inactivo, usuario sin credencial y dispositivo
no encontrado.
"""

from __future__ import annotations

import pytest

from app.core.errors import BioStarDeviceNotFoundError
from app.integrations.biostar.biostar_service import BioStarService

_DEVICE = {"id": "10", "name": "Gate Lane1", "ip": "172.17.110.49"}


class _FakeBioStarClient:
    def __init__(self, users=None, devices=None) -> None:
        self._users = users or []
        self._devices = devices or []
        self.logged_in = False

    def login(self) -> None:
        self.logged_in = True

    def logout(self) -> None:
        self.logged_in = False

    def get_users(self, limit: int = 100, offset: int = 0):
        return self._users

    def get_devices(self):
        return self._devices


def _service(users=None, devices=None) -> BioStarService:
    return BioStarService(client=_FakeBioStarClient(users, devices))


def _event(code: str, name: str, user_id: str = "42", user_name: str = "Juan") -> dict:
    return {
        "id": "evt-1",
        "datetime": "2026-06-22T14:00:00.000Z",
        "user": {"user_id": user_id, "name": user_name},
        "event_type": {"code": code, "name": name},
        "device": _DEVICE,
    }


# ---- login / dispositivos ----


def test_login_y_get_devices() -> None:
    service = _service(devices=[_DEVICE])
    devices = service.get_devices()
    assert len(devices) == 1
    assert devices[0]["name"] == "Gate Lane1"


def test_dispositivo_no_encontrado() -> None:
    service = _service(devices=[_DEVICE])
    with pytest.raises(BioStarDeviceNotFoundError):
        service.find_device("999.999")


# ---- validación de evento ----


def test_evento_permitido_usuario_activo_con_credencial() -> None:
    users = [
        {"user_id": "42", "name": "Juan", "card_count": "1", "disabled": "false", "expired": "false"}
    ]
    service = _service(users=users)
    result = service.validar_evento_acceso(
        _event("4102", "1:1 authentication succeeded (Card)")
    )
    assert result.permitir_paso is True
    assert result.estado == "ACTIVO"
    assert result.decision_sugerida == "PERMITIR"
    assert result.credentials.has_card is True
    assert result.device.ip == "172.17.110.49"


def test_evento_denegado() -> None:
    service = _service(users=[])
    result = service.validar_evento_acceso(_event("6400", "Access denied"))
    assert result.permitir_paso is False
    assert result.estado == "RECHAZADO_BIOSTAR"


def test_usuario_inactivo() -> None:
    users = [
        {"user_id": "42", "name": "Juan", "card_count": "1", "disabled": "true", "expired": "false"}
    ]
    service = _service(users=users)
    result = service.validar_evento_acceso(
        _event("4102", "1:1 authentication succeeded (Card)")
    )
    assert result.permitir_paso is False
    assert result.estado == "INACTIVO"


def test_usuario_sin_credencial() -> None:
    # Usuario activo, sin tarjeta/huella/rostro y evento genérico (sin método).
    users = [
        {
            "user_id": "42",
            "name": "Juan",
            "card_count": "0",
            "fingerprint_template_count": "0",
            "face_count": "0",
            "disabled": "false",
            "expired": "false",
        }
    ]
    service = _service(users=users)
    result = service.validar_evento_acceso(_event("4096", "1:1 authentication succeeded"))
    assert result.permitir_paso is False
    assert result.estado == "SIN_CREDENCIAL"


def test_usuario_no_en_cache_se_marca_visible() -> None:
    service = _service(users=[])  # padrón vacío
    result = service.validar_evento_acceso(
        _event("4867", "1:N identification succeeded (Face)")
    )
    assert result.estado == "NO_ENCONTRADO_EN_CACHE"
    assert result.permitir_paso is True
    assert result.credentials.has_face is True
