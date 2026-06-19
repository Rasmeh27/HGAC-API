"""Lógica de negocio sobre BioStar 2.

Mantiene un caché simple en memoria para evitar golpear el servidor en
cada verificación. El caché es responsabilidad del servicio, no del
cliente (mantiene al cliente neutro y testeable).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.core.errors import BioStarError, BioStarUserNotFoundError
from app.integrations.biostar.biostar_client import BioStarClient
from app.integrations.biostar.biostar_models import (
    BioStarUser,
    BioStarVerificationResult,
)


class BioStarService:
    def __init__(
        self,
        client: BioStarClient,
        cache_ttl_seconds: int = 60,
    ) -> None:
        self._client = client
        self._cache_ttl = cache_ttl_seconds
        self._users_cache: list[dict[str, Any]] = []
        self._users_cache_expires_at: float = 0.0

    def verificar_usuario(self, nombre_o_id: str) -> BioStarVerificationResult:
        """Verifica si un usuario existe en BioStar y está activo.

        Acepta tanto user_id como nombre. Devuelve un resultado tipado
        independiente del esquema crudo de la API.
        """
        if not nombre_o_id:
            return BioStarVerificationResult(
                found=False,
                reason="Identificador vacío",
                checked_at=datetime.now(timezone.utc),
            )

        try:
            user_raw = self._find_user(nombre_o_id)
        except BioStarUserNotFoundError:
            return BioStarVerificationResult(
                found=False,
                reason="Usuario no encontrado",
                checked_at=datetime.now(timezone.utc),
            )
        except BioStarError as exc:
            logger.warning("BioStar error verificando '{}': {}", nombre_o_id, exc)
            raise

        user = self._to_user_model(user_raw)
        return BioStarVerificationResult(
            found=True,
            is_active=user.is_active,
            user=user,
            reason=None if user.is_active else "Usuario inactivo",
            checked_at=datetime.now(timezone.utc),
        )

    # ---- lookups internos ----

    def _find_user(self, nombre_o_id: str) -> dict[str, Any]:
        for candidate in self._cached_users():
            if str(candidate.get("user_id")) == nombre_o_id:
                return candidate
            if str(candidate.get("name", "")).strip().lower() == nombre_o_id.strip().lower():
                return candidate

        # Si no estaba en caché, intentar resolverlo como ID directo.
        return self._client.get_user_detail(nombre_o_id)

    def _cached_users(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._users_cache and now < self._users_cache_expires_at:
            return self._users_cache

        logger.debug("BioStar: refrescando caché de usuarios")
        self._users_cache = self._client.get_users()
        self._users_cache_expires_at = now + self._cache_ttl
        return self._users_cache

    @staticmethod
    def _to_user_model(raw: dict[str, Any]) -> BioStarUser:
        disabled_flag = str(raw.get("disabled", "false")).lower() == "true"
        return BioStarUser(
            user_id=str(raw.get("user_id", "")),
            name=str(raw.get("name", "")),
            is_active=not disabled_flag,
            email=raw.get("email"),
            department=(raw.get("department") or {}).get("name") if isinstance(raw.get("department"), dict) else raw.get("department"),
        )
