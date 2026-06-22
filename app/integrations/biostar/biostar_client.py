"""Cliente HTTP para BioStar 2.

Refactor del script `test_biostar.py` original (procedural) a una clase
con responsabilidades claras:

* gestiona sesión persistente (`bs-session-id`),
* expone operaciones tipadas,
* no decide nada de negocio (eso vive en `BioStarService`),
* no conoce nada sobre cómo se loguea el resultado ni cómo se cachea.

SSL: BioStar 2 suele usar certificado autofirmado en LAN portuaria. La
desactivación de verificación SSL viene solo por configuración explícita.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
import urllib3
from loguru import logger

from app.core.errors import (
    BioStarAuthenticationError,
    BioStarError,
    BioStarUserNotFoundError,
)
from app.integrations.biostar import biostar_events


class BioStarClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        timeout_seconds: int = 10,
    ) -> None:
        if not base_url or not username or not password:
            raise BioStarError("BioStar host/username/password no configurados")

        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._verify_ssl = verify_ssl
        self._timeout = timeout_seconds

        self._session: Optional[requests.Session] = None
        self._session_id: Optional[str] = None

        if not verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ---- ciclo de vida de la sesión ----

    def login(self) -> None:
        session = requests.Session()
        session.verify = self._verify_ssl

        url = f"{self._base_url}/api/login"
        payload = {"User": {"login_id": self._username, "password": self._password}}
        logger.debug("BioStar login -> {}", url)
        response = session.post(url, json=payload, timeout=self._timeout)
        if response.status_code != 200:
            raise BioStarAuthenticationError(
                f"Login fallido ({response.status_code}): {response.text}"
            )

        session_id = response.headers.get("bs-session-id")
        if not session_id:
            raise BioStarAuthenticationError("BioStar no devolvió bs-session-id")

        session.headers.update({"bs-session-id": session_id})
        self._session = session
        self._session_id = session_id
        logger.info("BioStar: sesión iniciada")

    def logout(self) -> None:
        if not self._session:
            return
        try:
            self._session.post(f"{self._base_url}/api/logout", timeout=self._timeout)
        except requests.RequestException as exc:
            logger.warning("Error en logout BioStar (ignorado): {}", exc)
        finally:
            self._session.close()
            self._session = None
            self._session_id = None

    def __enter__(self) -> "BioStarClient":
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()

    # ---- operaciones ----

    def get_users(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        data = self._get("/api/users", params={"limit": limit, "offset": offset})
        return data.get("UserCollection", {}).get("rows", [])

    def get_users_total(self) -> int:
        data = self._get("/api/users", params={"limit": 1, "offset": 0})
        try:
            return int(data.get("UserCollection", {}).get("total", 0))
        except (TypeError, ValueError):
            return 0

    def get_user_detail(self, user_id: str) -> dict[str, Any]:
        data = self._get(f"/api/users/{user_id}")
        user = data.get("User")
        if not user:
            raise BioStarUserNotFoundError(f"Usuario {user_id} no existe")
        return user

    def get_devices(self) -> list[dict[str, Any]]:
        data = self._get("/api/devices")
        devices = data.get("DeviceCollection", {}).get("rows", [])
        # Resuelve la IP desde el nombre cuando el campo `ip` viene vacío.
        for device in devices:
            if not device.get("ip"):
                resolved = biostar_events.resolve_device_ip(device)
                if resolved:
                    device["resolved_ip"] = resolved
                    device["ip"] = resolved
        return devices

    def get_recent_events(
        self,
        limit: int = 20,
        device_id: Optional[str] = None,
        hours_back: int = 24,
    ) -> list[dict[str, Any]]:
        """Lee eventos recientes vía POST /api/events/search.

        Algunas versiones usan la columna ``device_id.id`` y otras ``device_id``;
        se intenta la primera y, si falla con el filtro de dispositivo, la segunda.
        """
        response = self._search_events(limit, device_id, "device_id.id", hours_back)
        if response.status_code >= 400 and device_id:
            response = self._search_events(limit, device_id, "device_id", hours_back)

        if response.status_code == 401:
            raise BioStarAuthenticationError("Sesión BioStar expirada")
        if response.status_code == 403:
            logger.warning(
                "BioStar rechazó la lectura de eventos (403); ¿rol sin permiso de logs?"
            )
            return []
        if response.status_code >= 400:
            raise BioStarError(
                f"BioStar events/search -> {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise BioStarError("Respuesta no-JSON en events/search") from exc
        return data.get("EventCollection", {}).get("rows", [])

    # ---- helpers ----

    def _search_events(
        self,
        limit: int,
        device_id: Optional[str],
        device_column: str,
        hours_back: int,
    ) -> requests.Response:
        if not self._session:
            raise BioStarError("BioStarClient no autenticado; llama a login() primero")

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=hours_back)
        conditions: list[dict[str, Any]] = [
            {
                "column": "datetime",
                "operator": 3,
                "values": [_iso_utc(start_time), _iso_utc(end_time)],
            }
        ]
        if device_id:
            conditions.append({"column": device_column, "operator": 0, "values": [str(device_id)]})

        payload = {
            "Query": {
                "limit": limit,
                "conditions": conditions,
                "orders": [{"column": "datetime", "descending": True}],
            }
        }
        try:
            return self._session.post(
                f"{self._base_url}/api/events/search", json=payload, timeout=self._timeout
            )
        except requests.Timeout as exc:
            raise BioStarError("Timeout en events/search") from exc
        except requests.RequestException as exc:
            raise BioStarError(f"Error de red en events/search: {exc}") from exc

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self._session:
            raise BioStarError("BioStarClient no autenticado; llama a login() primero")

        url = f"{self._base_url}{path}"
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.Timeout as exc:
            raise BioStarError(f"Timeout en {path}") from exc
        except requests.RequestException as exc:
            raise BioStarError(f"Error de red en {path}: {exc}") from exc

        if response.status_code == 401:
            raise BioStarAuthenticationError("Sesión BioStar expirada")
        if response.status_code >= 400:
            raise BioStarError(
                f"BioStar GET {path} -> {response.status_code}: {response.text}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise BioStarError(f"Respuesta no-JSON en {path}") from exc


def _iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
