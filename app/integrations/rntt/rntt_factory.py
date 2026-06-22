"""Selección del cliente RNTT según configuración.

Coexisten dos integraciones RNTT:
* ``RnttService``/``RnttClient`` (stub/Selenium) — placa→vehículo+pólizas, alimenta
  ``/crossing/evaluate``. Se conserva intacto.
* ``RnttAsmxService``/``RnttAsmxClient`` — API ASMX real (chofer/camión), expuesto en
  los endpoints de integraciones.
"""

from app.core.config import Settings, get_settings
from app.integrations.rntt.rntt_asmx_client import RnttAsmxClient
from app.integrations.rntt.rntt_asmx_service import RnttAsmxService
from app.integrations.rntt.rntt_client import RnttClient, SeleniumRnttClient, StubRnttClient
from app.integrations.rntt.rntt_service import RnttService


def build_rntt_client(settings: Settings | None = None) -> RnttClient:
    settings = settings or get_settings()
    if settings.rntt_use_stub:
        return StubRnttClient()
    return SeleniumRnttClient(
        portal_url=settings.rntt_portal_url,
        timeout_seconds=settings.rntt_timeout_seconds,
        headless=settings.rntt_headless,
    )


def build_rntt_service(settings: Settings | None = None) -> RnttService:
    return RnttService(client=build_rntt_client(settings))


def build_rntt_asmx_client(settings: Settings | None = None) -> RnttAsmxClient:
    settings = settings or get_settings()
    return RnttAsmxClient(
        base_url=settings.rntt_base_url,
        username=settings.rntt_username,
        password=settings.rntt_password,
        auth_mode=settings.rntt_auth_mode,
        timeout_seconds=settings.rntt_timeout_seconds,
        enable_diagnostic_fallbacks=settings.rntt_enable_diagnostic_fallbacks,
    )


def build_rntt_asmx_service(settings: Settings | None = None) -> RnttAsmxService:
    return RnttAsmxService(client=build_rntt_asmx_client(settings))
