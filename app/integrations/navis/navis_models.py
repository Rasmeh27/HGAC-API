"""Modelos limpios para el API Navis interna de HIT."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NavisTruck(BaseModel):
    id: str = ""
    license: str = ""
    license_state: str = ""
    license_expiration_date: str = ""
    internal_truck: Optional[bool] = None
    status: str = ""
    last_trk: str = ""
    last_truck_driver_name: str = ""
    life_cycle_state: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)


class NavisDriver(BaseModel):
    name: str = ""
    card_id: str = ""
    license: str = ""
    callup_id: str = ""
    license_state: str = ""
    status: str = ""
    internal: Optional[bool] = None
    life_cycle_state: str = ""
    raw: Dict[str, Any] = Field(default_factory=dict)


class NavisQueryResult(BaseModel):
    """Salida consolidada (mismo shape que el JSON que consume Ignition)."""

    source: str = "navis_api_hit"
    timestamp: datetime
    success: bool = False
    status: str = "ERROR"
    truck: Optional[NavisTruck] = None
    driver: Optional[NavisDriver] = None
    results: List[Dict[str, Any]] = Field(default_factory=list)
