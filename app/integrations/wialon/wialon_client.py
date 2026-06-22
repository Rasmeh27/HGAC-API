"""Cliente del API REST de Wialon (Gurtam).

Refactor de `test_wialon.py`: gestiona sesión (`sid`) con login por token y expone
operaciones tipadas. **Nunca** loguea el token. No interpreta geocercas ni decide
negocio (eso vive en `WialonService`/`wialon_geo`).
"""

from __future__ import annotations

import json
from typing import Any, Optional

import requests
from loguru import logger

from app.core.errors import WialonAuthenticationError, WialonError, WialonTimeoutError

# Trae todos los campos disponibles de la unidad (posición, sensores, etc.).
_ALL_FLAGS = 4294967295


class WialonClient:
    def __init__(self, host: str, token: str, timeout_seconds: int = 15) -> None:
        if not host:
            raise WialonError("WIALON_HOST no configurado")
        if not token:
            raise WialonError("WIALON_TOKEN no configurado")
        self._host = host.rstrip("/")
        self._api_url = f"{self._host}/wialon/ajax.html"
        self._token = token
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._sid: Optional[str] = None

    # ---- ciclo de vida ----

    def login(self) -> None:
        try:
            response = self._session.get(
                self._api_url,
                params={"svc": "token/login", "params": json.dumps({"token": self._token})},
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise WialonTimeoutError("Wialon no respondió al login") from exc
        except requests.RequestException as exc:
            raise WialonError(f"Error de red en login Wialon: {exc}") from exc

        data = self._json(response)
        if "error" in data:
            raise WialonAuthenticationError(
                f"Login Wialon falló (error {data['error']}): {data.get('reason', 'token inválido')}"
            )
        self._sid = data.get("eid", "")
        if not self._sid:
            raise WialonAuthenticationError("Wialon no devolvió sid (eid)")
        logger.info("Wialon: sesión iniciada (usuario {})", data.get("au", ""))

    def logout(self) -> None:
        if not self._sid:
            return
        try:
            self._call("core/logout", {})
        except WialonError as exc:
            logger.warning("Error en logout Wialon (ignorado): {}", exc)
        finally:
            self._sid = None

    def __enter__(self) -> "WialonClient":
        self.login()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()
        self._session.close()

    # ---- operaciones ----

    def get_units(self) -> list[dict[str, Any]]:
        data = self._call(
            "core/search_items",
            {
                "spec": {
                    "itemsType": "avl_unit",
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name",
                },
                "force": 1,
                "flags": _ALL_FLAGS,
                "from": 0,
                "to": 999,
            },
        )
        return data.get("items", []) if data else []

    def get_last_message(self, unit_id: int) -> Optional[dict[str, Any]]:
        data = self._call(
            "messages/load_last",
            {
                "itemId": unit_id,
                "lastTime": 0,
                "lastCount": 1,
                "flags": 1,
                "flagsMask": 255,
                "loadCount": 1,
            },
        )
        messages = (data or {}).get("messages", [])
        return messages[0] if messages else None

    def load_geofences(self) -> list[dict[str, Any]]:
        resources = self._call(
            "core/search_items",
            {
                "spec": {
                    "itemsType": "avl_resource",
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name",
                },
                "force": 1,
                "flags": _ALL_FLAGS,
                "from": 0,
                "to": 99,
            },
        )
        if not resources:
            return []

        zones: list[dict[str, Any]] = []
        for resource in resources.get("items", []):
            zone_ids = []
            for zone_id in (resource.get("zl", {}) or {}).keys():
                try:
                    zone_ids.append(int(zone_id))
                except (TypeError, ValueError):
                    pass
            if not zone_ids:
                continue
            details = self._call(
                "resource/get_zone_data",
                {"itemId": resource.get("id"), "col": zone_ids, "flags": 31},
            )
            if isinstance(details, list):
                zones.extend(details)
        return zones

    # ---- helpers ----

    def _call(self, action: str, params: dict[str, Any]) -> Any:
        if not self._sid:
            raise WialonError("WialonClient no autenticado; llama a login() primero")
        try:
            response = self._session.get(
                self._api_url,
                params={"svc": action, "params": json.dumps(params), "sid": self._sid},
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            raise WialonTimeoutError(f"Wialon no respondió en {action}") from exc
        except requests.RequestException as exc:
            raise WialonError(f"Error de red en {action}: {exc}") from exc

        data = self._json(response)
        if isinstance(data, dict) and "error" in data:
            error_code = data["error"]
            # 1 = sesión inválida/expirada.
            if error_code == 1:
                raise WialonAuthenticationError(f"Sesión Wialon inválida en {action}")
            raise WialonError(
                f"Wialon {action} error {error_code}: {data.get('reason', '')}"
            )
        return data

    @staticmethod
    def _json(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise WialonError("Respuesta no-JSON de Wialon") from exc
