"""Construcción del `WialonService`."""

from app.core.config import Settings, get_settings
from app.integrations.wialon.wialon_client import WialonClient
from app.integrations.wialon.wialon_service import WialonService


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_wialon_client(settings: Settings | None = None) -> WialonClient:
    settings = settings or get_settings()
    return WialonClient(
        host=settings.wialon_host,
        token=settings.wialon_token,
        timeout_seconds=settings.wialon_timeout_seconds,
    )


def build_wialon_service(settings: Settings | None = None) -> WialonService:
    settings = settings or get_settings()
    return WialonService(
        client=build_wialon_client(settings),
        terminal_geofence_names=_csv(settings.wialon_terminal_geofence_names),
        gate_zone_keywords=_csv(settings.wialon_gate_zone_keywords),
        online_seconds=settings.wialon_online_seconds,
    )
