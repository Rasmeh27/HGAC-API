"""Endpoint del módulo LPR: lectura de placa sobre un frame de cámara.

Vive aparte del legacy `lpr_routes.py` (`/lpr/read`, `/lpr/debug/snapshot`) para
no romperlo: este expone el contrato formal nuevo bajo `/api/v1/lpr`.
La lógica OCR pesada vive en el servicio/motor; aquí solo se mapean errores a
códigos HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import lpr_read_service_provider, settings_provider
from app.core.config import Settings
from app.core.errors import CameraError, CameraNotFoundError
from app.modules.lpr.lpr_models import LprReadRequest, LprReadResponse
from app.modules.lpr.lpr_service import LprService

router = APIRouter(prefix="/api/v1/lpr", tags=["LPR"])


@router.post(
    "/reads",
    response_model=LprReadResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_lpr_read(
    request: LprReadRequest,
    lpr_service: LprService = Depends(lpr_read_service_provider),
    settings: Settings = Depends(settings_provider),
) -> LprReadResponse:
    """Solicita una lectura de placa usando un frame de la cámara indicada.

    Devuelve siempre JSON estructurado (placa detectada o no). Los problemas de
    cámara se mapean a 404 (no existe) y 503 (no entrega frame).
    """
    if not settings.lpr_enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LPR module is disabled (LPR_ENABLED=false).",
        )

    try:
        return lpr_service.read_plate(request)
    except CameraNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except CameraError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Camera {request.camera_id} did not return a valid frame.",
        ) from exc
