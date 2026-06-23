"""Tests del endpoint GET /biostar/events/latest (snapshot del monitor local).

Sin BioStar real: se sobreescribe `settings_provider` para apuntar
`biostar_local_output_path` a un archivo temporal y se ejercitan los 4 caminos:
archivo ausente, objeto JSON válido, JSON que no es objeto y JSON inválido.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import settings_provider
from app.core.config import get_settings
from app.main import app

client = TestClient(app)
LATEST_URL = "/biostar/events/latest"


def _settings_pointing_to(path) -> object:
    return get_settings().model_copy(update={"biostar_local_output_path": str(path)})


@pytest.fixture
def latest_path(tmp_path):
    """Apunta el endpoint a un JSON temporal y limpia el override al terminar."""
    target = tmp_path / "hgac_biostar_local.json"
    app.dependency_overrides[settings_provider] = lambda: _settings_pointing_to(target)
    yield target
    app.dependency_overrides.pop(settings_provider, None)


def test_latest_archivo_inexistente_devuelve_503(latest_path):
    # No se crea el archivo: el monitor aún no ha publicado nada.
    response = client.get(LATEST_URL)
    assert response.status_code == 503
    assert "no existe" in response.json()["detail"]


def test_latest_objeto_json_valido_devuelve_200(latest_path):
    payload = {
        "status": "WAITING_FOR_EVENT",
        "source": "biostar_local",
        "trigger": False,
        "device": {"id": "10", "name": "Gate Lane1", "ip": "172.17.110.49"},
    }
    latest_path.write_text(json.dumps(payload), encoding="utf-8")

    response = client.get(LATEST_URL)
    assert response.status_code == 200
    assert response.json() == payload


def test_latest_json_no_objeto_devuelve_503(latest_path):
    # Una lista es JSON válido pero no un objeto -> contrato roto -> 503.
    latest_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    response = client.get(LATEST_URL)
    assert response.status_code == 503
    assert "no contiene un objeto JSON" in response.json()["detail"]


def test_latest_json_invalido_devuelve_503(latest_path):
    latest_path.write_text("{ esto no es json", encoding="utf-8")

    response = client.get(LATEST_URL)
    assert response.status_code == 503
    assert "No se pudo leer" in response.json()["detail"]


def test_verify_sigue_registrado():
    # Regresión: el endpoint existente no debe romperse al añadir /events/latest.
    paths = {route.path for route in app.routes}
    assert "/biostar/verify" in paths
    assert "/biostar/events/latest" in paths
