"""Modelos limpios para unidades GPS de Wialon."""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class WialonUnitSummary(BaseModel):
    id: Optional[int] = None
    nombre: str = ""
    guid: str = ""
    unique_id: str = ""
    clase_objeto: Any = ""
    clase_objeto_desc: str = ""
    lat: float = 0.0
    lon: float = 0.0
    velocidad: float = 0.0
    rumbo: float = 0.0
    altitud: float = 0.0
    satelites: int = 0
    ultimo_reporte: str = ""
    ultimo_reporte_ts: int = 0
    gps_age_seconds: Optional[int] = None
    online: bool = False
    geofences: List[str] = Field(default_factory=list)
    geofence_name: str = ""
    inside_terminal: bool = False
    inside_gate_zone: bool = False


class WialonUnitsResult(BaseModel):
    timestamp: datetime
    status: str = "OK"
    selection_mode: str = ""
    target_unit: str = ""
    total_units: int = 0
    selected_unit: Optional[WialonUnitSummary] = None
    unidades: List[WialonUnitSummary] = Field(default_factory=list)
