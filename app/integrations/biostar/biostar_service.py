"""Lógica de negocio sobre BioStar 2.

Mantiene un caché simple en memoria para evitar golpear el servidor en
cada verificación. El caché es responsabilidad del servicio, no del
cliente (mantiene al cliente neutro y testeable).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

from app.core.errors import (
    BioStarAuthenticationError,
    BioStarDeviceNotFoundError,
    BioStarError,
    BioStarUserNotFoundError,
)
from app.integrations.biostar import biostar_events
from app.integrations.biostar.biostar_client import BioStarClient
from app.integrations.biostar.biostar_models import (
    BioStarAccessValidation,
    BioStarCredentials,
    BioStarDeviceRef,
    BioStarUser,
    BioStarVerificationResult,
)

_T = TypeVar("_T")


class BioStarService:
    def __init__(
        self,
        client: BioStarClient,
        cache_ttl_seconds: int = 60,
        display_timezone: str = "America/Santo_Domingo",
        events_hours_back: int = 24,
    ) -> None:
        self._client = client
        self._cache_ttl = cache_ttl_seconds
        self._timezone = display_timezone
        self._events_hours_back = events_hours_back
        self._users_cache: list[dict[str, Any]] = []
        self._users_cache_expires_at: float = 0.0
        self._logged_in = False

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
        return self._call(lambda: self._client.get_user_detail(nombre_o_id))

    def _cached_users(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        if self._users_cache and now < self._users_cache_expires_at:
            return self._users_cache

        logger.debug("BioStar: refrescando caché de usuarios")
        self._users_cache = self._call(lambda: self._client.get_users())
        self._users_cache_expires_at = now + self._cache_ttl
        return self._users_cache

    # ---- dispositivos ----

    def get_devices(self) -> list[dict[str, Any]]:
        return self._call(lambda: self._client.get_devices())

    def find_device(self, target: str) -> dict[str, Any]:
        """Resuelve un dispositivo por id, IP o parte del nombre."""
        for device in self.get_devices():
            if biostar_events.device_matches(device, target):
                return device
        raise BioStarDeviceNotFoundError(f"Dispositivo BioStar no encontrado: {target}")

    # ---- eventos ----

    def get_recent_events(
        self,
        limit: int = 20,
        target_device: Optional[str] = None,
        only_validation_events: bool = False,
    ) -> list[dict[str, Any]]:
        device_id = None
        if target_device:
            device = self.find_device(target_device)
            device_id = str(device.get("id", "")) or None

        events = self._call(
            lambda: self._client.get_recent_events(
                limit=limit, device_id=device_id, hours_back=self._events_hours_back
            )
        )
        if target_device:
            events = [
                e
                for e in events
                if biostar_events.device_matches(biostar_events.event_device(e), target_device)
            ]
        if only_validation_events:
            events = [e for e in events if biostar_events.event_requires_validation(e)]
        return events

    # ---- validación de evento de acceso ----

    def validar_evento_acceso(self, event: dict[str, Any]) -> BioStarAccessValidation:
        """Decide PASA / NO PASA para un evento de acceso de BioStar.

        Replica la lógica del script: denegado por el lector → RECHAZADO_BIOSTAR;
        usuario inactivo/expirado → INACTIVO; activo sin credencial → SIN_CREDENCIAL;
        activo con credencial → ACTIVO; sin detalle en caché → NO_ENCONTRADO_EN_CACHE.
        """
        user = biostar_events.event_user(event)
        user_id = str(user.get("user_id", ""))
        user_name = user.get("name", "Desconocido")
        event_label = biostar_events.event_type_label(event)

        if biostar_events.event_was_denied(event):
            creds = biostar_events.merge_event_credentials(
                biostar_events.user_credentials({}), event
            )
            return self._build_validation(
                event,
                permitir_paso=False,
                estado="RECHAZADO_BIOSTAR",
                motivo=f"BioStar rechazó el acceso: {event_label}",
                credentials=creds,
                user_id=user_id,
                nombre=user_name,
            )

        cached_user = self._find_cached_user_by_id(user_id)
        if cached_user:
            creds = biostar_events.merge_event_credentials(
                biostar_events.user_credentials(cached_user), event
            )
            disabled = str(cached_user.get("disabled", "")).lower() == "true"
            expired = str(cached_user.get("expired", "")).lower() == "true"
            nombre = cached_user.get("name", user_name)
            if disabled or expired:
                return self._build_validation(
                    event,
                    permitir_paso=False,
                    estado="INACTIVO",
                    motivo="Usuario inactivo o expirado en BioStar",
                    credentials=creds,
                    user_id=user_id,
                    nombre=nombre,
                )
            if not creds["credential_trigger"]:
                return self._build_validation(
                    event,
                    permitir_paso=False,
                    estado="SIN_CREDENCIAL",
                    motivo="Usuario activo, pero sin tarjeta, huella ni rostro registrados",
                    credentials=creds,
                    user_id=user_id,
                    nombre=nombre,
                )
            return self._build_validation(
                event,
                permitir_paso=True,
                estado="ACTIVO",
                motivo="Usuario activo con credencial registrada en BioStar",
                credentials=creds,
                user_id=user_id,
                nombre=nombre,
            )

        # BioStar autenticó pero no hay detalle local: se marca visible para que
        # Ignition aplique reglas adicionales.
        creds = biostar_events.merge_event_credentials(biostar_events.user_credentials({}), event)
        return self._build_validation(
            event,
            permitir_paso=True,
            estado="NO_ENCONTRADO_EN_CACHE",
            motivo="BioStar autenticó el usuario; no se encontró detalle local",
            credentials=creds,
            user_id=user_id,
            nombre=user_name,
        )

    def _build_validation(
        self,
        event: dict[str, Any],
        *,
        permitir_paso: bool,
        estado: str,
        motivo: str,
        credentials: dict[str, Any],
        user_id: str,
        nombre: str,
    ) -> BioStarAccessValidation:
        device = biostar_events.event_device(event)
        return BioStarAccessValidation(
            permitir_paso=permitir_paso,
            decision_sugerida="PERMITIR" if permitir_paso else "NO_PERMITIR",
            estado=estado,
            motivo=motivo,
            credentials=BioStarCredentials(
                has_card=bool(credentials.get("has_card")),
                has_fingerprint=bool(credentials.get("has_fingerprint")),
                has_face=bool(credentials.get("has_face")),
                event_method=credentials.get("event_method", ""),
            ),
            event_time=biostar_events.format_biostar_datetime(
                event.get("datetime", ""), self._timezone
            ),
            event_type=biostar_events.event_type_label(event),
            event_type_display=biostar_events.event_type_display(event),
            event_type_code=biostar_events.event_type_code(event),
            user_id=user_id,
            nombre=nombre,
            device=BioStarDeviceRef(
                id=biostar_events.device_value(device, "id"),
                name=biostar_events.device_value(device, "name"),
                ip=biostar_events.device_value(device, "ip")
                or biostar_events.resolve_device_ip(device),
            ),
        )

    def _find_cached_user_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        if not user_id:
            return None
        for user in self._cached_users():
            if str(user.get("user_id", "")) == str(user_id):
                return user
        return None

    # ---- sesión ----

    def _ensure_session(self) -> None:
        if not self._logged_in:
            self._client.login()
            self._logged_in = True

    def _call(self, fn: Callable[[], _T]) -> _T:
        """Ejecuta una operación garantizando sesión; re-loguea una vez ante 401."""
        self._ensure_session()
        try:
            return fn()
        except BioStarAuthenticationError:
            logger.info("BioStar: sesión expirada, re-autenticando")
            self._logged_in = False
            self._ensure_session()
            return fn()

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
