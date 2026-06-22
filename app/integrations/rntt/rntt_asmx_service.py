"""Servicio de negocio sobre el API RNTT ASMX.

Toma los dicts crudos del `RnttAsmxClient`, normaliza fechas .NET, interpreta
estados (catálogo observado, no oficial) y arma modelos limpios. Implementa
también la consulta combinada chofer + camión del script `test_rntt_combinado.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.integrations.rntt import rntt_normalization as norm
from app.integrations.rntt.rntt_asmx_client import RnttAsmxClient
from app.integrations.rntt.rntt_models import (
    RnttCombinedResult,
    RnttDriver,
    RnttTruck,
)

_DRIVER_TIPOS = {"rntt", "licencia", "cedula"}
_TRUCK_TIPOS = {"placa", "rotulo", "chasis", "rfid"}


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


class RnttAsmxService:
    def __init__(self, client: RnttAsmxClient) -> None:
        self._client = client

    # ---- consultas simples ----

    def consultar_chofer(self, tipo: str, valor: str) -> RnttDriver | None:
        """tipo en {rntt, licencia, cedula}."""
        if tipo not in _DRIVER_TIPOS:
            raise ValueError(f"Tipo de chofer inválido: {tipo} (use {_DRIVER_TIPOS})")
        raw = self._client.consultar(f"chofer_{tipo}", _clean(valor))
        data = norm.normalize_dates(raw)
        if not norm.is_driver_payload(data):
            logger.info("RNTT: chofer no disponible para {}={}", tipo, valor)
            return None
        return self._to_driver(data)

    def consultar_camion(self, tipo: str, valor: str) -> RnttTruck | None:
        """tipo en {placa, rotulo, chasis, rfid}."""
        if tipo not in _TRUCK_TIPOS:
            raise ValueError(f"Tipo de camión inválido: {tipo} (use {_TRUCK_TIPOS})")
        raw = self._client.consultar(f"truck_{tipo}", _clean(valor))
        data = norm.normalize_dates(raw)
        if not norm.is_truck_payload(data):
            logger.info("RNTT: camión no disponible para {}={}", tipo, valor)
            return None
        return self._to_truck(data)

    # ---- consulta combinada (chofer + camión) ----

    def consulta_combinada(self, tipo: str, valor: str) -> RnttCombinedResult:
        """Encadena chofer<->camión por Rótulo, replicando test_rntt_combinado.py.

        - tipo de chofer (rntt/licencia/cedula): consulta chofer; si trae Rótulo,
          consulta el camión por ese Rótulo.
        - tipo de camión (placa/rotulo/chasis/rfid): consulta camión; si trae
          Rótulo y la consulta no fue por rótulo, reconsulta por Rótulo para dejar
          evidencia del camión asociado.
        - El API ASMX no expone "chofer por rótulo/placa": si solo hay camión, el
          chofer queda EXPLÍCITAMENTE como no disponible.
        """
        driver: RnttDriver | None = None
        truck: RnttTruck | None = None
        related: list[dict[str, str]] = []
        notes: list[str] = []

        if tipo in _DRIVER_TIPOS:
            driver = self.consultar_chofer(tipo, valor)
            if driver and driver.rotulo:
                related.append({"tipo": "truck_rotulo", "valor": driver.rotulo})
                truck = self.consultar_camion("rotulo", driver.rotulo)
        elif tipo in _TRUCK_TIPOS:
            truck = self.consultar_camion(tipo, valor)
            if truck and truck.rotulo and tipo != "rotulo":
                related.append({"tipo": "truck_rotulo", "valor": truck.rotulo})
                truck_by_rotulo = self.consultar_camion("rotulo", truck.rotulo)
                if truck_by_rotulo:
                    truck = truck_by_rotulo
        else:
            raise ValueError(f"Tipo de consulta combinada inválido: {tipo}")

        if truck and not driver:
            notes.append(
                "RNTT ASMX no expone un método 'chofer por rótulo/placa'; el chofer "
                "queda como no disponible para esta consulta."
            )

        return RnttCombinedResult(
            queried_at=datetime.now(timezone.utc),
            driver=driver,
            truck=truck,
            driver_available=driver is not None,
            truck_available=truck is not None,
            related_queries=related,
            notes=notes,
        )

    # ---- mapeo crudo -> modelo ----

    @staticmethod
    def _to_driver(data: dict[str, Any]) -> RnttDriver:
        interpreted, reason = norm.interpret_driver_status(data)
        first = _clean(data.get("NAMEFIRST"))
        last = _clean(data.get("NAMELAST"))
        return RnttDriver(
            rntt=_clean(data.get("RNTT")),
            nombre_completo=f"{first} {last}".strip(),
            cedula=_clean(data.get("NumeroCedula")),
            licencia=_clean(data.get("NumeroLicencia")),
            tipo_licencia=_clean(data.get("TipoLicencia")),
            expiracion_licencia=_clean(data.get("ExpiracionLicencia")),
            vencimiento_afiliacion=_clean(data.get("ENDDATE")),
            rotulo=_clean(data.get("Rotulo")),
            empresa=_clean(data.get("Empresa") or data.get("Sindicato")),
            rnc=_clean(data.get("RNC")),
            validity=data.get("VALIDITY"),
            validity_label=norm.validity_label(data.get("VALIDITY")),
            bdgsts=data.get("bdgsts"),
            bdgsts_label=norm.bdgsts_label(data.get("bdgsts")),
            interpreted_status=interpreted,
            interpreted_reason=reason,
            raw=data,
        )

    @staticmethod
    def _to_truck(data: dict[str, Any]) -> RnttTruck:
        interpreted, reason = norm.interpret_truck_status(data)
        return RnttTruck(
            truck_name=_clean(data.get("TruckName")),
            chasis=_clean(data.get("TruckChasisNumber")),
            permiso=_clean(data.get("TruckPermitNumber")),
            propietario=_clean(data.get("TruckOwnerName")),
            color=_clean(data.get("TruckColor")),
            tipo_carga=_clean(data.get("TipoCarga")),
            estado=data.get("Estado"),
            estado_label=interpreted,
            estado_reason=reason,
            rotulo=_clean(data.get("Rotulo")),
            institucion=_clean(data.get("Institucion")),
            poliza_carga=_clean(data.get("PolizaCarga")),
            rfid=_clean(data.get("RFID")),
            fecha_creacion=_clean(data.get("FechaCreacion")),
            raw=data,
        )
