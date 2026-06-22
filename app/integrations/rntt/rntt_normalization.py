"""Normalización e interpretación de respuestas RNTT (API ASMX).

Funciones puras, sin IO ni red: reciben dicts crudos del portal y devuelven
valores limpios. Esto las hace trivialmente testeables.

IMPORTANTE — catálogos observados, NO oficiales: RNTT no entregó el catálogo
oficial de códigos de estado. Los mapeos de abajo se infirieron comparando la
respuesta del API contra lo que mostraba el portal RNTT durante el PoC. No deben
tratarse como verdad absoluta; documentan lo observado.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

_DOTNET_DATE_RE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d+)?\)/$")

# Estado de camión observado en el portal RNTT durante el PoC (no oficial).
TRUCK_ESTADO_LABELS: dict[Any, str] = {
    2: "Activo",
    "2": "Activo",
    5: "Cancelado",
    "5": "Cancelado",
}

# VALIDITY=6 se observó tanto en choferes activos como restringidos: por sí solo
# no decide nada. Se conserva como dato informativo.
DRIVER_VALIDITY_LABELS: dict[Any, str] = {
    6: "Observado en activos y restringidos",
    "6": "Observado en activos y restringidos",
}

# Estado bruto de chofer (bdgsts) inferido por comparación contra el portal (no oficial).
DRIVER_BDGSTS_LABELS: dict[Any, str] = {
    0: "Activo",
    "0": "Activo",
    1: "Restringido - Evaluacion vencida",
    "1": "Restringido - Evaluacion vencida",
    7: "Restringido - Evaluacion vencida",
    "7": "Restringido - Evaluacion vencida",
    9: "Restringido",
    "9": "Restringido",
}

_PENDING_LABEL = "Pendiente catalogo oficial"


def parse_dotnet_date(value: str) -> str | None:
    """Convierte ``/Date(1700000000000)/`` (.NET) a ``YYYY-MM-DD HH:MM:SS`` local."""
    match = _DOTNET_DATE_RE.match(value)
    if not match:
        return None
    try:
        millis = int(match.group(1))
        dt = datetime.fromtimestamp(millis / 1000, tz=timezone.utc).astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OverflowError, OSError):
        return None


def normalize_dates(value: Any) -> Any:
    """Recorre dict/list/str y reemplaza fechas .NET por strings legibles."""
    if isinstance(value, dict):
        return {key: normalize_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_dates(item) for item in value]
    if isinstance(value, str):
        parsed = parse_dotnet_date(value)
        return parsed if parsed else value
    return value


def _parse_normalized_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def is_past_date(value: Any) -> bool:
    dt = _parse_normalized_datetime(value)
    if not dt:
        return False
    return dt < datetime.now()


def validity_label(value: Any) -> str:
    return DRIVER_VALIDITY_LABELS.get(value, _PENDING_LABEL)


def bdgsts_label(value: Any) -> str:
    return DRIVER_BDGSTS_LABELS.get(value, _PENDING_LABEL)


def interpret_driver_status(data: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (estado_interpretado, motivo). Catálogo observado, no oficial.

    Espera el dict ya normalizado (fechas .NET resueltas).
    """
    reasons: list[str] = []
    if is_past_date(data.get("ExpiracionLicencia")):
        reasons.append("licencia vencida")
    if is_past_date(data.get("ENDDATE")):
        reasons.append("afiliacion vencida")

    validity = data.get("VALIDITY")
    bdgsts = data.get("bdgsts")

    if str(bdgsts) in ("1", "7"):
        reasons.append("evaluacion vencida")
    elif str(bdgsts) == "9" and not reasons:
        reasons.append("estado bruto restringido")

    if reasons:
        return "Restringido", "; ".join(reasons)
    if str(bdgsts) == "0":
        return "Activo", f"bdgsts={bdgsts} ({bdgsts_label(bdgsts)}); fechas vigentes"
    return (
        "Pendiente catalogo",
        f"VALIDITY={validity} ({validity_label(validity)}); "
        f"bdgsts={bdgsts} ({bdgsts_label(bdgsts)})",
    )


def interpret_truck_status(data: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (estado_interpretado, motivo). Catálogo observado, no oficial."""
    estado = data.get("Estado")
    estado_label = TRUCK_ESTADO_LABELS.get(estado)
    if estado_label:
        return estado_label, f"Estado={estado} observado en portal RNTT como {estado_label}"
    rotulo_estado = data.get("RotuloEstado")
    if rotulo_estado:
        return "Pendiente catalogo", f"Estado={estado}; RotuloEstado={rotulo_estado}"
    return "Pendiente catalogo", f"Estado={estado}"


def is_driver_payload(data: Any) -> bool:
    return isinstance(data, dict) and ("RNTT" in data or "NumeroLicencia" in data)


def is_truck_payload(data: Any) -> bool:
    return isinstance(data, dict) and ("TruckName" in data or "TruckChasisNumber" in data)
