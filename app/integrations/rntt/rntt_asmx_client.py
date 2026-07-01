"""Cliente HTTP del API RNTT (servicio ASMX).

Refactor del script `test_rntt_api.py` (procedural, con prints y ~19 intentos de
autenticación en cascada) a una clase con responsabilidades claras:

* habla solo con el servicio ASMX (no interpreta estados ni decide negocio),
* usa por defecto el modo de autenticación CONFIRMADO en producción
  (headers ``Username``/``Password`` por GET),
* mantiene los intentos de diagnóstico del script original **detrás de un flag
  explícito de debug** (``enable_diagnostic_fallbacks``), apagado por defecto, y
  nunca incluye no-auth salvo en ese modo de diagnóstico.

La lógica OAuth de Navis que venía mezclada en el script original NO vive aquí:
está en `app/integrations/navis/`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime
from typing import Any

import requests
from loguru import logger

from app.core.errors import RnttError, RnttTimeoutError

# tipo lógico -> (método ASMX, nombre de parámetro documentado)
ENDPOINTS: dict[str, tuple[str, str]] = {
    "chofer_rntt": ("getChoferByRNTTCode", "RNTT"),
    "chofer_licencia": ("getChoferByLicencia", "licencia"),
    "chofer_cedula": ("getChoferByCedula", "cedula"),
    "truck_placa": ("getTruckByPlaca", "placa"),
    "truck_rotulo": ("getTruckByRotulo", "rotulo"),
    "truck_chasis": ("getTruckByChasis", "chasis"),
    "truck_rfid": ("getTruckByRFID", "placa"),  # la doc llama "placa" al valor RFID
    "chasis": ("getChasis", "Chasis"),
    "chasis_rfid": ("getChasisByRFID", "RFID"),
    "institucion_rnc": ("getInstitutionByRNC", "rnc"),
    "poliza": ("getPoliza", "poliza"),
}

_BLOCKED_MARKERS = ("no tiene acceso", "unauthorized", "authorization")


class RnttAsmxClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        auth_mode: str = "header",
        timeout_seconds: int = 30,
        enable_diagnostic_fallbacks: bool = False,
    ) -> None:
        # El WebService RNTT exige autenticación (sin credenciales responde
        # "No tiene acceso a este servicio"): base_url, username y password son
        # OBLIGATORIOS. La capa de dependencias traduce este error a 503.
        if not base_url or not username or not password:
            raise RnttError("RNTT base_url/username/password no configurados")

        self._base_url = base_url.rstrip("/")
        # El servicio respondía también con doble slash en algunos despliegues.
        self._base_url_double = self._base_url.replace("/WebService1.asmx", "//WebService1.asmx")
        self._username = username
        self._password = password
        self._auth_mode = auth_mode
        self._timeout = timeout_seconds
        self._enable_fallbacks = enable_diagnostic_fallbacks
        self._session = requests.Session()

    # ---- operaciones de alto nivel (chofer) ----

    def get_chofer_by_rntt(self, code: str) -> Any:
        return self._consultar("chofer_rntt", code)

    def get_chofer_by_licencia(self, licencia: str) -> Any:
        return self._consultar("chofer_licencia", licencia)

    def get_chofer_by_cedula(self, cedula: str) -> Any:
        return self._consultar("chofer_cedula", cedula)

    # ---- operaciones de alto nivel (camión) ----

    def get_truck_by_placa(self, placa: str) -> Any:
        return self._consultar("truck_placa", placa)

    def get_truck_by_rotulo(self, rotulo: str) -> Any:
        return self._consultar("truck_rotulo", rotulo)

    def get_truck_by_chasis(self, chasis: str) -> Any:
        return self._consultar("truck_chasis", chasis)

    def get_truck_by_rfid(self, rfid: str) -> Any:
        return self._consultar("truck_rfid", rfid)

    def get_chasis(self, chasis: str) -> Any:
        return self._consultar("chasis", chasis)

    def get_institution_by_rnc(self, rnc: str) -> Any:
        return self._consultar("institucion_rnc", rnc)

    def consultar(self, tipo: str, valor: str) -> Any:
        """Consulta genérica por tipo lógico (ver `ENDPOINTS`)."""
        return self._consultar(tipo, valor)

    # ---- internos ----

    def _consultar(self, tipo: str, valor: str) -> Any:
        if tipo not in ENDPOINTS:
            raise RnttError(f"Tipo de consulta RNTT desconocido: {tipo}")
        endpoint, param_name = ENDPOINTS[tipo]
        logger.info("RNTT ASMX: consulta {} ({}={})", endpoint, param_name, valor)
        return self._get(endpoint, {param_name: valor})

    def _primary_attempt(self, params: dict[str, str]) -> tuple[str, dict[str, str], dict[str, str]]:
        """(método HTTP, headers, params) de la estrategia confirmada."""
        if self._auth_mode == "hmac":
            return "GET", self._hmac_headers(), params
        # Modo confirmado en producción: Username/Password como headers simples.
        return "GET", {"Username": self._username, "Password": self._password}, params

    def _hmac_headers(self, token_format: str = "hex") -> dict[str, str]:
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = self._username + time_str
        digest = hmac.new(self._password.encode("utf-8"), message.encode("utf-8"), hashlib.sha256)
        if token_format == "hex_upper":
            token = digest.hexdigest().upper()
        elif token_format == "base64":
            token = base64.b64encode(digest.digest()).decode("ascii")
        else:
            token = digest.hexdigest()
        return {"Username": self._username, "Time": time_str, "Token": token}

    def _attempts(self, params: dict[str, str]) -> list[tuple[str, str, dict[str, str], dict[str, str]]]:
        """Lista ordenada de intentos. Sin fallbacks: solo la estrategia primaria.

        Con `enable_diagnostic_fallbacks` se añaden variantes del script original
        (útiles solo para diagnosticar cambios del servidor RNTT). Incluyen
        intentos no-auth: por eso quedan tras el flag explícito de debug.
        """
        method, headers, request_params = self._primary_attempt(params)
        attempts: list[tuple[str, str, dict[str, str], dict[str, str]]] = [
            ("primary", method, headers, request_params),
        ]
        if not self._enable_fallbacks:
            return attempts

        user, pwd = self._username, self._password
        hmac_hex = self._hmac_headers("hex")
        hmac_upper = self._hmac_headers("hex_upper")
        hmac_b64 = self._hmac_headers("base64")
        attempts += [
            ("headers_username_password_post", "POST", {"Username": user, "Password": pwd}, params),
            ("headers_lower_user_password", "GET", {"username": user, "password": pwd}, params),
            ("query_username_password", "GET", {}, {**params, "Username": user, "Password": pwd}),
            ("headers_hmac_hex", "GET", hmac_hex, params),
            ("headers_hmac_hex_upper", "GET", hmac_upper, params),
            ("headers_hmac_base64", "GET", hmac_b64, params),
            ("query_hmac_hex", "GET", {}, {**params, **hmac_hex}),
            # Intentos no-auth: SOLO diagnóstico.
            ("get_no_auth", "GET", {}, params),
            ("post_form_no_auth", "POST", {}, params),
        ]
        return attempts

    def _get(self, endpoint: str, params: dict[str, str]) -> Any:
        urls = [f"{self._base_url}/{endpoint}", f"{self._base_url_double}/{endpoint}"]
        last_text = ""
        for url in urls:
            for label, method, headers, request_params in self._attempts(params):
                try:
                    if method == "POST":
                        response = self._session.post(
                            url, data=request_params, headers=headers, timeout=self._timeout
                        )
                    else:
                        response = self._session.get(
                            url, params=request_params, headers=headers, timeout=self._timeout
                        )
                except requests.Timeout as exc:
                    raise RnttTimeoutError(f"RNTT no respondió a tiempo en {endpoint}") from exc
                except requests.RequestException as exc:
                    logger.debug("RNTT intento '{}' falló: {}", label, exc)
                    continue

                last_text = response.text or ""
                if response.status_code == 200 and not self._looks_blocked(last_text):
                    return self._parse(response)
                logger.debug(
                    "RNTT {} [{}]: {} (bloqueado/no-200)", endpoint, label, response.status_code
                )

        raise RnttError(
            f"RNTT no aceptó la consulta {endpoint}. Última respuesta: {last_text[:200]}"
        )

    @staticmethod
    def _looks_blocked(text: str) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in _BLOCKED_MARKERS)

    @staticmethod
    def _parse(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            # ASMX puede devolver XML envolviendo un string JSON; se preserva crudo.
            return {"raw": (response.text or "").strip()}

    def close(self) -> None:
        self._session.close()
