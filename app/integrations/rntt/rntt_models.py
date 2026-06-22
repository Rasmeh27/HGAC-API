"""Modelos limpios para datos de RNTT (Registro Nacional de Tránsito Terrestre)."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RnttPolicy(BaseModel):
    name: str
    expires_at: Optional[date] = None
    is_valid: bool = True


class RnttVehicle(BaseModel):
    plate: str
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    year: Optional[int] = None


class RnttResult(BaseModel):
    plate: str
    status: str = Field(..., description="ACTIVE | INACTIVE | NOT_FOUND")
    vehicle: Optional[RnttVehicle] = None
    policies: List[RnttPolicy] = []
    queried_at: datetime


# --- API ASMX real (chofer/camión) ---
# NOTA: `interpreted_status`/`estado_label` provienen de catálogos OBSERVADOS en
# el PoC, no del catálogo oficial RNTT (ver rntt_normalization.py).


class RnttDriver(BaseModel):
    rntt: str = ""
    nombre_completo: str = ""
    cedula: str = ""
    licencia: str = ""
    tipo_licencia: str = ""
    expiracion_licencia: str = ""
    vencimiento_afiliacion: str = ""
    rotulo: str = ""
    empresa: str = ""
    rnc: str = ""
    validity: Optional[Any] = None
    validity_label: str = ""
    bdgsts: Optional[Any] = None
    bdgsts_label: str = ""
    interpreted_status: str = ""
    interpreted_reason: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)


class RnttTruck(BaseModel):
    truck_name: str = ""
    chasis: str = ""
    permiso: str = ""
    propietario: str = ""
    color: str = ""
    tipo_carga: str = ""
    estado: Optional[Any] = None
    estado_label: str = ""
    estado_reason: str = ""
    rotulo: str = ""
    institucion: str = ""
    poliza_carga: str = ""
    rfid: str = ""
    fecha_creacion: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)


class RnttCombinedResult(BaseModel):
    source: str = "rntt_api_asmx"
    queried_at: datetime
    driver: Optional[RnttDriver] = None
    truck: Optional[RnttTruck] = None
    driver_available: bool = False
    truck_available: bool = False
    related_queries: List[Dict[str, str]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
