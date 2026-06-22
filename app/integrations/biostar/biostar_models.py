"""Modelos limpios para datos de BioStar 2.

Estos modelos son los que se exponen al resto del backend. No exponemos
la respuesta cruda del API porque su esquema cambia entre versiones.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class BioStarUser(BaseModel):
    user_id: str
    name: str
    is_active: bool = True
    email: Optional[str] = None
    department: Optional[str] = None


class BioStarDevice(BaseModel):
    device_id: str
    name: str
    ip: Optional[str] = None
    status: Optional[str] = None


class BioStarDeviceRef(BaseModel):
    id: str = ""
    name: str = ""
    ip: str = ""


class BioStarCredentials(BaseModel):
    has_card: bool = False
    has_fingerprint: bool = False
    has_face: bool = False
    event_method: str = ""


class BioStarVerificationResult(BaseModel):
    """Resultado de `BioStarService.verificar_usuario`."""

    found: bool
    is_active: bool = False
    user: Optional[BioStarUser] = None
    reason: Optional[str] = Field(
        default=None,
        description="Motivo cuando found=False o is_active=False",
    )
    checked_at: datetime


class BioStarAccessValidation(BaseModel):
    """Resultado de validar un evento de acceso BioStar (PASA / NO PASA).

    Conserva los campos equivalentes al script original para que Ignition
    consuma el contrato sin cambios.
    """

    permitir_paso: bool
    decision_sugerida: str
    estado: str
    motivo: str
    credentials: BioStarCredentials
    event_time: str = ""
    event_type: str = ""
    event_type_display: str = ""
    event_type_code: str = ""
    user_id: str = ""
    nombre: str = ""
    device: BioStarDeviceRef = Field(default_factory=BioStarDeviceRef)
