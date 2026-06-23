"""Endpoints de BioStar 2 para el PoC HGAC.

Incluye:
* POST /biostar/verify        -> verifica usuario/estado en BioStar.
* GET  /biostar/events/latest -> último evento publicado por el monitor local.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import biostar_service_provider, settings_provider
from app.api.schemas import BioStarVerifyRequest, BioStarVerifyResponse
from app.core.config import Settings
from app.core.errors import BioStarAuthenticationError, BioStarError
from app.integrations.biostar.biostar_service import BioStarService

router = APIRouter(prefix="/biostar", tags=["BioStar"])


@router.get("/events/latest")
def latest_local_event(
    settings: Settings = Depends(settings_provider),
) -> dict:
    """Devuelve el último evento publicado por el monitor BioStar local.

    El monitor (`scripts/monitor_biostar_local.py`) escribe el snapshot JSON en
    `settings.biostar_local_output_path` (env `BIOSTAR_LOCAL_OUTPUT_PATH`). Si el
    archivo no existe, no puede leerse o no es un objeto JSON, se responde 503
    con un mensaje claro para que el consumidor (Ignition) sepa que el monitor
    aún no tiene datos.
    """
    path = Path(settings.biostar_local_output_path)

    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Monitor BioStar sin datos: no existe {path}",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No se pudo leer el ultimo evento BioStar: {exc}",
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El snapshot BioStar no contiene un objeto JSON",
        )

    return payload


@router.post("/verify", response_model=BioStarVerifyResponse)
def verify_user(
    payload: BioStarVerifyRequest,
    biostar_service: BioStarService = Depends(biostar_service_provider),
) -> BioStarVerifyResponse:
    try:
        result = biostar_service.verificar_usuario(payload.nombre_o_id)
    except BioStarAuthenticationError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except BioStarError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    return BioStarVerifyResponse(
        found=result.found,
        is_active=result.is_active,
        user_id=result.user.user_id if result.user else None,
        name=result.user.name if result.user else None,
        department=result.user.department if result.user else None,
        reason=result.reason,
        checked_at=result.checked_at,
    )
