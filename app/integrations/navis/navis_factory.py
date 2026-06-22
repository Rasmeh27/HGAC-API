"""Construcción del `NavisService`."""

from app.core.config import Settings, get_settings
from app.integrations.navis.navis_client import NavisClient
from app.integrations.navis.navis_service import NavisService


def build_navis_client(settings: Settings | None = None) -> NavisClient:
    settings = settings or get_settings()
    return NavisClient(
        api_base=settings.navis_api_base,
        token_path=settings.navis_token_path,
        token_url=settings.navis_token_url,
        grant_type=settings.navis_grant_type,
        client_id=settings.navis_client_id,
        client_secret=settings.navis_client_secret,
        username=settings.navis_username,
        password=settings.navis_password,
        scope=settings.navis_scope,
        timeout_seconds=settings.navis_timeout_seconds,
    )


def build_navis_service(settings: Settings | None = None) -> NavisService:
    return NavisService(client=build_navis_client(settings))
