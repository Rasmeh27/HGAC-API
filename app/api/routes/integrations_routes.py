"""Endpoints de las integraciones del handoff (RNTT ASMX, BioStar, Navis, Wialon).

Todo bajo `/api/v1/integrations` (misma convención que los módulos nuevos del
backend). La lógica vive en los servicios; aquí solo se mapean errores a HTTP.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import (
    biostar_service_provider,
    navis_service_provider,
    rntt_asmx_service_provider,
    settings_provider,
    wialon_service_provider,
)
from app.api.integrations_schemas import (
    RNTT_DRIVER_TIPOS,
    BioStarDevicesResponse,
    BioStarEventsRecentRequest,
    BioStarEventsRecentResponse,
    BioStarValidateEventRequest,
    IntegrationHealth,
    IntegrationsHealthResponse,
    NavisQueryRequest,
    RnttCombinedQueryRequest,
    RnttQueryRequest,
    RnttQueryResponse,
)
from app.core.config import Settings
from app.core.errors import (
    BioStarAuthenticationError,
    BioStarDeviceNotFoundError,
    IntegrationError,
    NavisAuthenticationError,
    NavisTimeoutError,
    RnttTimeoutError,
    WialonAuthenticationError,
    WialonTimeoutError,
)
from app.integrations.biostar.biostar_models import (
    BioStarAccessValidation,
    BioStarDeviceRef,
)
from app.integrations.biostar.biostar_service import BioStarService
from app.integrations.navis.navis_models import NavisQueryResult
from app.integrations.navis.navis_service import NavisService
from app.integrations.rntt.rntt_asmx_service import RnttAsmxService
from app.integrations.rntt.rntt_models import RnttCombinedResult
from app.integrations.wialon.wialon_models import WialonUnitsResult, WialonUnitSummary
from app.integrations.wialon.wialon_service import WialonService

router = APIRouter(prefix="/api/v1/integrations", tags=["Integrations"])

_TIMEOUT_ERRORS = (RnttTimeoutError, NavisTimeoutError, WialonTimeoutError)
_AUTH_ERRORS = (BioStarAuthenticationError, NavisAuthenticationError, WialonAuthenticationError)


def _to_http(exc: IntegrationError) -> HTTPException:
    """Mapea un error de integración al código HTTP coherente con el resto del API."""
    if isinstance(exc, _TIMEOUT_ERRORS):
        return HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc))
    if isinstance(exc, BioStarDeviceNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, _AUTH_ERRORS):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))


# ---- Health ----


@router.get("/health", response_model=IntegrationsHealthResponse)
def integrations_health(
    settings: Settings = Depends(settings_provider),
) -> IntegrationsHealthResponse:
    """Indica qué integraciones están configuradas. No expone secretos."""
    rntt_ok = bool(settings.rntt_base_url and settings.rntt_username and settings.rntt_password)
    biostar_ok = bool(
        settings.biostar_host and settings.biostar_username and settings.biostar_password
    )
    navis_ok = bool(
        (settings.navis_api_base or settings.navis_token_url)
        and settings.navis_username
        and settings.navis_password
    )
    wialon_ok = bool(settings.wialon_token and settings.wialon_host)

    rntt_detail = f"ASMX chofer/camión (auth={settings.rntt_auth_mode})"
    if settings.rntt_enable_diagnostic_fallbacks:
        rntt_detail += " [diagnóstico ON]"

    return IntegrationsHealthResponse(
        integrations={
            "rntt": IntegrationHealth(configured=rntt_ok, detail=rntt_detail),
            "biostar": IntegrationHealth(configured=biostar_ok, detail="control de acceso BioStar 2"),
            "navis": IntegrationHealth(configured=navis_ok, detail="API HIT (OAuth password grant)"),
            "wialon": IntegrationHealth(configured=wialon_ok, detail="GPS Gurtam"),
        }
    )


# ---- RNTT ----


@router.post("/rntt/query", response_model=RnttQueryResponse)
def rntt_query(
    payload: RnttQueryRequest,
    service: RnttAsmxService = Depends(rntt_asmx_service_provider),
) -> RnttQueryResponse:
    try:
        if payload.tipo in RNTT_DRIVER_TIPOS:
            driver = service.consultar_chofer(payload.tipo, payload.valor)
            if driver:
                return RnttQueryResponse(
                    tipo=payload.tipo, valor=payload.valor, kind="driver", driver=driver
                )
        else:
            truck = service.consultar_camion(payload.tipo, payload.valor)
            if truck:
                return RnttQueryResponse(
                    tipo=payload.tipo, valor=payload.valor, kind="truck", truck=truck
                )
    except IntegrationError as exc:
        raise _to_http(exc) from exc
    return RnttQueryResponse(tipo=payload.tipo, valor=payload.valor, kind="not_found")


@router.post("/rntt/combined-query", response_model=RnttCombinedResult)
def rntt_combined_query(
    payload: RnttCombinedQueryRequest,
    service: RnttAsmxService = Depends(rntt_asmx_service_provider),
) -> RnttCombinedResult:
    try:
        return service.consulta_combinada(payload.tipo, payload.valor)
    except IntegrationError as exc:
        raise _to_http(exc) from exc


# ---- Navis ----


@router.post("/navis/query", response_model=NavisQueryResult)
def navis_query(
    payload: NavisQueryRequest,
    service: NavisService = Depends(navis_service_provider),
) -> NavisQueryResult:
    try:
        return service.consultar(truck=payload.truck, driver=payload.driver)
    except IntegrationError as exc:
        raise _to_http(exc) from exc


# ---- BioStar ----


@router.get("/biostar/devices", response_model=BioStarDevicesResponse)
def biostar_devices(
    service: BioStarService = Depends(biostar_service_provider),
) -> BioStarDevicesResponse:
    try:
        devices = service.get_devices()
    except IntegrationError as exc:
        raise _to_http(exc) from exc
    refs = [
        BioStarDeviceRef(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            ip=str(d.get("ip") or d.get("resolved_ip") or ""),
        )
        for d in devices
    ]
    return BioStarDevicesResponse(count=len(refs), devices=refs)


@router.post("/biostar/events/recent", response_model=BioStarEventsRecentResponse)
def biostar_recent_events(
    payload: BioStarEventsRecentRequest,
    service: BioStarService = Depends(biostar_service_provider),
) -> BioStarEventsRecentResponse:
    try:
        events = service.get_recent_events(
            limit=payload.limit,
            target_device=payload.target_device,
            only_validation_events=payload.only_validation_events,
        )
    except IntegrationError as exc:
        raise _to_http(exc) from exc
    return BioStarEventsRecentResponse(count=len(events), events=events)


@router.post("/biostar/validate-event", response_model=BioStarAccessValidation)
def biostar_validate_event(
    payload: BioStarValidateEventRequest,
    service: BioStarService = Depends(biostar_service_provider),
) -> BioStarAccessValidation:
    try:
        event = payload.event
        if event is None:
            events = service.get_recent_events(
                limit=1, target_device=payload.target_device, only_validation_events=True
            )
            if not events:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    "No hay eventos de validación recientes para el dispositivo indicado.",
                )
            event = events[0]
        return service.validar_evento_acceso(event)
    except IntegrationError as exc:
        raise _to_http(exc) from exc


# ---- Wialon ----


@router.get("/wialon/units", response_model=WialonUnitsResult)
def wialon_units(
    target: str = Query("", description="ID interno, unique ID/IMEI o parte del nombre"),
    online_seconds: Optional[int] = Query(None, ge=1),
    service: WialonService = Depends(wialon_service_provider),
) -> WialonUnitsResult:
    try:
        return service.get_units_summary(target=target, online_seconds=online_seconds)
    except IntegrationError as exc:
        raise _to_http(exc) from exc


@router.get("/wialon/unit/{unit_id_or_name}", response_model=WialonUnitSummary)
def wialon_unit(
    unit_id_or_name: str,
    online_seconds: Optional[int] = Query(None, ge=1),
    service: WialonService = Depends(wialon_service_provider),
) -> WialonUnitSummary:
    try:
        unit = service.get_unit(unit_id_or_name, online_seconds=online_seconds)
    except IntegrationError as exc:
        raise _to_http(exc) from exc
    if unit is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Unidad Wialon no encontrada: {unit_id_or_name}"
        )
    return unit
