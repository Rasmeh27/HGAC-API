"""DTOs request/response para los endpoints de integraciones (`/api/v1/integrations`).

Los nombres de campo de respuesta se alinean con lo que consumen los scripts de
Ignition (`HGAC_01`/`HGAC_02`) para que el ingest sea de mapeo mínimo. Varias
respuestas reutilizan directamente los modelos de dominio (ya son contratos limpios).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.integrations.biostar.biostar_models import BioStarAccessValidation, BioStarDeviceRef
from app.integrations.navis.navis_models import NavisQueryResult
from app.integrations.rntt.rntt_models import RnttCombinedResult, RnttDriver, RnttTruck
from app.integrations.wialon.wialon_models import WialonUnitsResult, WialonUnitSummary

__all__ = [
    "IntegrationHealth",
    "IntegrationsHealthResponse",
    "RnttQueryRequest",
    "RnttQueryResponse",
    "RnttCombinedQueryRequest",
    "NavisQueryRequest",
    "BioStarDevicesResponse",
    "BioStarEventsRecentRequest",
    "BioStarEventsRecentResponse",
    "BioStarValidateEventRequest",
    # Re-exports de modelos de dominio usados como response_model:
    "BioStarAccessValidation",
    "NavisQueryResult",
    "RnttCombinedResult",
    "WialonUnitsResult",
    "WialonUnitSummary",
]

# Tipos lógicos de consulta RNTT por categoría.
RNTT_DRIVER_TIPOS = ("rntt", "licencia", "cedula")
RNTT_TRUCK_TIPOS = ("placa", "rotulo", "chasis", "rfid")
RnttTipo = Literal["rntt", "licencia", "cedula", "placa", "rotulo", "chasis", "rfid"]


# ---- Health ----


class IntegrationHealth(BaseModel):
    configured: bool
    detail: str = ""


class IntegrationsHealthResponse(BaseModel):
    status: str = "ok"
    integrations: Dict[str, IntegrationHealth]


# ---- RNTT ----


class RnttQueryRequest(BaseModel):
    tipo: RnttTipo
    valor: str = Field(..., min_length=1)


class RnttQueryResponse(BaseModel):
    tipo: str
    valor: str
    kind: Literal["driver", "truck", "not_found"]
    driver: Optional[RnttDriver] = None
    truck: Optional[RnttTruck] = None


class RnttCombinedQueryRequest(BaseModel):
    tipo: RnttTipo
    valor: str = Field(..., min_length=1)


# ---- Navis ----


class NavisQueryRequest(BaseModel):
    truck: Optional[str] = None
    driver: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "NavisQueryRequest":
        if not self.truck and not self.driver:
            raise ValueError("Indique truck, driver o ambos.")
        return self


# ---- BioStar ----


class BioStarDevicesResponse(BaseModel):
    count: int
    devices: List[BioStarDeviceRef]


class BioStarEventsRecentRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    target_device: Optional[str] = None
    only_validation_events: bool = False


class BioStarEventsRecentResponse(BaseModel):
    count: int
    events: List[Dict[str, Any]]


class BioStarValidateEventRequest(BaseModel):
    """Valida un evento de acceso.

    - `event`: payload crudo del evento BioStar a validar, o
    - `target_device`: si no se envía `event`, se toma el evento de validación más
      reciente de ese dispositivo y se valida.
    """

    event: Optional[Dict[str, Any]] = None
    target_device: Optional[str] = None

    @model_validator(mode="after")
    def _need_event_or_device(self) -> "BioStarValidateEventRequest":
        if self.event is None and not self.target_device:
            raise ValueError("Envíe 'event' o 'target_device'.")
        return self
