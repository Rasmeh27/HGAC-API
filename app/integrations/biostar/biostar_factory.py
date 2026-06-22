"""Construcción del `BioStarService` (perfil remoto y local)."""

from app.core.config import Settings, get_settings
from app.integrations.biostar.biostar_client import BioStarClient
from app.integrations.biostar.biostar_service import BioStarService


def build_biostar_client(settings: Settings | None = None) -> BioStarClient:
    settings = settings or get_settings()
    return BioStarClient(
        base_url=settings.biostar_base_url,
        username=settings.biostar_username,
        password=settings.biostar_password,
        verify_ssl=settings.biostar_verify_ssl,
        timeout_seconds=settings.biostar_timeout_seconds,
    )


def build_biostar_service(settings: Settings | None = None) -> BioStarService:
    settings = settings or get_settings()
    return BioStarService(
        client=build_biostar_client(settings),
        cache_ttl_seconds=settings.biostar_users_cache_ttl_seconds,
        display_timezone=settings.biostar_display_timezone,
        events_hours_back=settings.biostar_events_hours_back,
    )


def build_biostar_local_client(settings: Settings | None = None) -> BioStarClient:
    """Cliente apuntando al BioStar local (lector facial en esta PC)."""
    settings = settings or get_settings()
    return BioStarClient(
        base_url=settings.biostar_local_base_url,
        username=settings.biostar_local_user,
        password=settings.biostar_local_password,
        verify_ssl=settings.biostar_verify_ssl,
        timeout_seconds=settings.biostar_timeout_seconds,
    )


def build_biostar_local_service(settings: Settings | None = None) -> BioStarService:
    settings = settings or get_settings()
    return BioStarService(
        client=build_biostar_local_client(settings),
        cache_ttl_seconds=settings.biostar_users_cache_ttl_seconds,
        display_timezone=settings.biostar_display_timezone,
        events_hours_back=settings.biostar_events_hours_back,
    )
