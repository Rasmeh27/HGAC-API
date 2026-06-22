"""Cliente del API Navis interna de HIT.

Refactor de `test_navis_api.py`: OAuth password grant + Bearer token. El token se
cachea en memoria hasta poco antes de expirar (`expires_in`) para no pedir uno por
request. **Nunca** se loguea el token ni las credenciales.
"""

from __future__ import annotations

import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from loguru import logger

from app.core.errors import NavisAuthenticationError, NavisError, NavisTimeoutError

# Margen (s) antes de la expiración real para refrescar el token.
_TOKEN_REFRESH_MARGIN = 30
# TTL conservador si el servidor no envía expires_in.
_TOKEN_DEFAULT_TTL = 300


def _join_url(base_url: str, path: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))


class NavisClient:
    def __init__(
        self,
        api_base: str,
        token_path: str = "oauth/token",
        token_url: str = "",
        grant_type: str = "password",
        client_id: str = "",
        client_secret: str = "",
        username: str = "",
        password: str = "",
        scope: str = "",
        timeout_seconds: int = 25,
    ) -> None:
        if not api_base and not token_url:
            raise NavisError("Navis api_base o token_url no configurados")
        if not username or not password:
            raise NavisError("Navis username/password no configurados")

        self._api_base = api_base.rstrip("/") if api_base else ""
        self._token_url = token_url or _join_url(self._api_base, token_path)
        self._grant_type = grant_type
        self._client_id = client_id
        self._client_secret = client_secret
        self._username = username
        self._password = password
        self._scope = scope
        self._timeout = timeout_seconds
        self._session = requests.Session()

        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0

    # ---- token ----

    def _get_token(self) -> str:
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token

        payload = {
            "grant_type": self._grant_type,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "username": self._username,
            "password": self._password,
        }
        if self._scope:
            payload["scope"] = self._scope

        logger.info("Navis: solicitando token OAuth para usuario {}", self._username)
        try:
            response = self._session.post(self._token_url, data=payload, timeout=self._timeout)
        except requests.Timeout as exc:
            raise NavisTimeoutError("Navis no respondió al solicitar token") from exc
        except requests.RequestException as exc:
            raise NavisError(f"Error de red al solicitar token Navis: {exc}") from exc

        if response.status_code not in (200, 201):
            raise NavisAuthenticationError(
                f"Navis token -> {response.status_code}: {response.text[:200]}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise NavisAuthenticationError("Respuesta de token Navis no es JSON") from exc

        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise NavisAuthenticationError("Navis no devolvió access_token")

        try:
            ttl = int(data.get("expires_in", _TOKEN_DEFAULT_TTL))
        except (TypeError, ValueError):
            ttl = _TOKEN_DEFAULT_TTL
        self._token = str(token)
        self._token_expires_at = now + max(0, ttl - _TOKEN_REFRESH_MARGIN)
        logger.debug("Navis: token OK (ttl≈{}s)", ttl)
        return self._token

    # ---- consultas ----

    def get_truck_info(self, value: str) -> dict[str, Any]:
        return self._get("truck-info", value)

    def get_driver_info(self, value: str) -> dict[str, Any]:
        return self._get("driver-info", value)

    def _get(self, endpoint: str, value: str) -> dict[str, Any]:
        token = self._get_token()
        url = _join_url(self._api_base, f"api/navis/{endpoint}/{value}")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        logger.info("Navis: consulta {} valor={}", endpoint, value)
        try:
            response = self._session.get(url, headers=headers, timeout=self._timeout)
        except requests.Timeout as exc:
            raise NavisTimeoutError(f"Navis no respondió en {endpoint}") from exc
        except requests.RequestException as exc:
            raise NavisError(f"Error de red en Navis {endpoint}: {exc}") from exc

        if response.status_code == 401:
            # Token rechazado: invalida la caché para forzar refresh en el próximo intento.
            self._token = None
            raise NavisAuthenticationError(f"Navis rechazó el token en {endpoint} (401)")

        try:
            body: Any = response.json()
        except ValueError:
            body = {"raw": (response.text or "").strip()}

        return {
            "http_status": response.status_code,
            "endpoint": endpoint,
            "value": value,
            "url": url,
            "response": body,
        }

    def close(self) -> None:
        self._session.close()
