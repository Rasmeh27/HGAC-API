"""Tests del cliente/servicio RNTT ASMX y la normalización (solo mocks, sin red).

Se inyecta un cliente falso en `RnttAsmxService` y se ejercitan los casos del
handoff: chofer activo/restringido, camión activo/cancelado, respuesta vacía y
normalización de fechas .NET.
"""

from __future__ import annotations

from app.integrations.rntt import rntt_normalization as norm
from app.integrations.rntt.rntt_asmx_service import RnttAsmxService

# .NET date para 2030-01-01 (futuro, no vencido).
_DOTNET_FUTURE = "/Date(1893456000000)/"


class _FakeRnttClient:
    """Cliente RNTT falso: mapea tipo lógico -> respuesta cruda."""

    def __init__(self, responses: dict[str, object]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def consultar(self, tipo: str, valor: str):
        self.calls.append(tipo)
        return self._responses.get(tipo, {})


def _service(responses: dict[str, object]) -> RnttAsmxService:
    return RnttAsmxService(client=_FakeRnttClient(responses))


# ---- chofer ----


def test_chofer_activo() -> None:
    service = _service(
        {
            "chofer_rntt": {
                "RNTT": "2091",
                "NAMEFIRST": "Juan",
                "NAMELAST": "Perez",
                "NumeroLicencia": "05100147353",
                "bdgsts": "0",
                "Rotulo": "A528",
                "ExpiracionLicencia": _DOTNET_FUTURE,
            }
        }
    )
    driver = service.consultar_chofer("rntt", "2091")
    assert driver is not None
    assert driver.rntt == "2091"
    assert driver.nombre_completo == "Juan Perez"
    assert driver.interpreted_status == "Activo"
    # La fecha .NET se normalizó (ya no es /Date(...)/).
    assert not driver.expiracion_licencia.startswith("/Date(")


def test_chofer_restringido() -> None:
    service = _service({"chofer_licencia": {"NumeroLicencia": "123", "bdgsts": "9"}})
    driver = service.consultar_chofer("licencia", "123")
    assert driver is not None
    assert driver.interpreted_status == "Restringido"
    assert "restringido" in driver.interpreted_reason.lower()


# ---- camión ----


def test_camion_activo() -> None:
    service = _service({"truck_placa": {"TruckName": "L312278", "Estado": "2", "Rotulo": "A528"}})
    truck = service.consultar_camion("placa", "L312278")
    assert truck is not None
    assert truck.truck_name == "L312278"
    assert truck.estado_label == "Activo"


def test_camion_cancelado() -> None:
    service = _service({"truck_placa": {"TruckName": "L999999", "Estado": "5"}})
    truck = service.consultar_camion("placa", "L999999")
    assert truck is not None
    assert truck.estado_label == "Cancelado"


# ---- respuesta vacía ----


def test_respuesta_vacia_devuelve_none() -> None:
    service = _service({"chofer_cedula": {}})
    assert service.consultar_chofer("cedula", "00112345678") is None


def test_truck_respuesta_no_camion_devuelve_none() -> None:
    # Un payload que no parece camión (sin TruckName/TruckChasisNumber) → None.
    service = _service({"truck_rotulo": {"mensaje": "sin datos"}})
    assert service.consultar_camion("rotulo", "A528") is None


# ---- combinada (chofer -> camión por rótulo) ----


def test_combinada_encadena_chofer_a_camion_por_rotulo() -> None:
    service = _service(
        {
            "chofer_rntt": {"RNTT": "2091", "Rotulo": "A528", "bdgsts": "0"},
            "truck_rotulo": {"TruckName": "L312278", "Estado": "2", "Rotulo": "A528"},
        }
    )
    result = service.consulta_combinada("rntt", "2091")
    assert result.driver_available is True
    assert result.truck_available is True
    assert result.truck.truck_name == "L312278"
    assert {"tipo": "truck_rotulo", "valor": "A528"} in result.related_queries


def test_combinada_por_placa_sin_chofer_lo_marca_no_disponible() -> None:
    service = _service({"truck_placa": {"TruckName": "L312278", "Estado": "2"}})
    result = service.consulta_combinada("placa", "L312278")
    assert result.truck_available is True
    assert result.driver_available is False
    assert any("no disponible" in note.lower() for note in result.notes)


# ---- normalización de fechas .NET (helpers puros) ----


def test_parse_dotnet_date() -> None:
    assert norm.parse_dotnet_date(_DOTNET_FUTURE) is not None
    assert norm.parse_dotnet_date("no-es-fecha") is None


def test_normalize_dates_recursivo() -> None:
    data = {"a": _DOTNET_FUTURE, "b": {"c": _DOTNET_FUTURE}, "d": "texto"}
    out = norm.normalize_dates(data)
    assert not out["a"].startswith("/Date(")
    assert not out["b"]["c"].startswith("/Date(")
    assert out["d"] == "texto"
