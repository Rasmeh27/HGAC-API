"""Cliente del portal RNTT.

Se define una interfaz `RnttClient` para que el servicio no dependa de
Selenium directamente. La implementación con Selenium vive aparte y
solo se importa cuando realmente se usa (evita arrancar el driver en la
PoC inicial y mantiene el resto del backend testeable).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.core.errors import RnttError, RnttPlateNotFoundError


class RnttClient(ABC):
    @abstractmethod
    def fetch_plate(self, plate: str) -> dict[str, Any]:
        """Consulta una placa y devuelve un dict crudo del portal.

        Implementaciones concretas deben mapear errores a:
        * `RnttTimeoutError`
        * `RnttPlateNotFoundError`
        * `RnttError` para fallos del portal
        """


class StubRnttClient(RnttClient):
    """Implementación de stub para desarrollo y tests.

    Devuelve datos sintéticos para que el resto del flujo (LPR -> RNTT ->
    reglas de cruce) sea ejercitable sin levantar Selenium.
    """

    def fetch_plate(self, plate: str) -> dict[str, Any]:
        normalized = plate.strip().upper()
        if not normalized:
            raise RnttError("Placa vacía")

        # Caso fácil de simular: una placa termina en 'X' -> no encontrada.
        if normalized.endswith("X"):
            raise RnttPlateNotFoundError(normalized)

        return {
            "plate": normalized,
            "status": "ACTIVE",
            "vehicle": {
                "brand": "Daihatsu",
                "model": "Hijet",
                "color": "Blanco",
                "year": 2018,
            },
            "policies": [
                {"name": "Seguro obligatorio", "expires_at": "2026-12-31", "is_valid": True},
                {"name": "Revisión técnica", "expires_at": "2026-09-30", "is_valid": True},
            ],
            "queried_at": datetime.now(timezone.utc).isoformat(),
        }


class SeleniumRnttClient(RnttClient):
    """Esqueleto para integrar el script Selenium existente.

    Mantener Selenium aislado en su propio módulo permite que el resto
    del backend siga siendo testeable y rápido. Esta implementación es
    un punto de entrada vacío que se completará cuando se porte el
    script real del compañero. Por ahora lanza `RnttError` si se invoca.
    """

    def __init__(
        self,
        portal_url: str,
        timeout_seconds: int = 30,
        headless: bool = True,
    ) -> None:
        if not portal_url:
            raise RnttError("RNTT_PORTAL_URL no configurado")
        self._portal_url = portal_url
        self._timeout = timeout_seconds
        self._headless = headless

    def fetch_plate(self, plate: str) -> dict[str, Any]:  # pragma: no cover - placeholder
        logger.warning("SeleniumRnttClient aún no implementado, usa StubRnttClient")
        raise RnttError(
            "Integración Selenium con RNTT pendiente de portar desde el script original"
        )
        # Cuando se porte el script existente:
        # 1. Construir webdriver con opciones headless según self._headless.
        # 2. Navegar a self._portal_url y rellenar formulario.
        # 3. Parsear resultado y devolverlo como dict.
        # 4. Convertir TimeoutException -> RnttTimeoutError.
