"""Helpers puros para interpretar eventos de BioStar 2.

Sin IO ni red: reciben dicts crudos de eventos/usuarios del API y devuelven
valores limpios. Esto los hace directamente testeables. El servicio
(`BioStarService`) los usa para construir la validación de acceso.

`EVENT_TYPE_LABELS` mapea códigos T_EVTTYP oficiales de BioStar 2 a texto legible
(fuente: PDF "BioStar 2.5 Event Type T_EVTTYP").
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Eventos que disparan validación de acceso (texto del evento normalizado a minúsculas).
ACCESS_EVENT_KEYWORDS = (
    "access granted",
    "authenticated",
    "authentication success",
    "authentication succeeded",
    "card authenticated",
    "face authenticated",
    "verify success",
    "verify succeeded",
    "identification succeeded",
    "success",
    "succeed",
    "succeeded",
    "duress",
    "granted",
    "permitido",
    "autorizado",
)

DENIED_EVENT_KEYWORDS = (
    "access denied",
    "authentication failed",
    "verify failed",
    "failed",
    "denied",
    "fail",
    "rechazado",
    "denegado",
)

# Catálogo oficial T_EVTTYP de BioStar 2 (subconjunto relevante para el PoC).
EVENT_TYPE_LABELS: dict[str, str] = {
    "4096": "1:1 authentication succeeded",
    "4097": "1:1 authentication succeeded (ID + PIN)",
    "4098": "1:1 authentication succeeded (ID + Fingerprint)",
    "4099": "1:1 authentication succeeded (ID + Fingerprint + PIN)",
    "4100": "1:1 authentication succeeded (ID + Face)",
    "4101": "1:1 authentication succeeded (ID + Face + PIN)",
    "4102": "1:1 authentication succeeded (Card)",
    "4103": "1:1 authentication succeeded (Card + PIN)",
    "4104": "1:1 authentication succeeded (Card + Fingerprint)",
    "4105": "1:1 authentication succeeded (Card + Fingerprint + PIN)",
    "4106": "1:1 authentication succeeded (Card + Face)",
    "4107": "1:1 authentication succeeded (Card + Face + PIN)",
    "4352": "Authentication failed",
    "4353": "Authentication failed (ID)",
    "4354": "Authentication failed (Card)",
    "4355": "Authentication failed (PIN)",
    "4356": "Authentication failed (Fingerprint)",
    "4357": "Authentication failed (Face)",
    "4608": "VERIFY_DURESS - Succeed to verify user under duress",
    "4864": "1:N identification succeeded",
    "4865": "1:N identification succeeded (Fingerprint)",
    "4866": "1:N identification succeeded (Fingerprint + PIN)",
    "4867": "1:N identification succeeded (Face)",
    "4868": "1:N identification succeeded (Face + PIN)",
    "5120": "1:N identification failed",
    "5124": "1:N identification failed (Fingerprint)",
    "5125": "1:N identification failed (Face)",
    "6400": "Access denied",
    "6401": "Access denied (Access group / door schedule)",
    "6402": "Access denied (User disabled)",
    "6403": "Access denied (User expired)",
    "6404": "Access denied (Card blacklisted)",
    "6405": "Access denied (Anti-passback violation)",
    "6410": "Access denied (Face recognition failure)",
    "6411": "Access denied (Face detection failure)",
    "6412": "Fake fingerprint detected",
    "20480": "Door unlocked",
    "20736": "Door locked",
    "20992": "Door opened",
    "21248": "Door closed",
}

# Códigos que delatan la credencial usada en el evento (cuando el texto no basta).
_CARD_CODES = {"4102", "4103", "4104", "4105", "4106", "4107", "4354"}
_FINGERPRINT_CODES = {"4098", "4099", "4104", "4105", "4356", "4865", "5124"}
_FACE_CODES = {"4100", "4101", "4106", "4107", "4357", "4867", "5125"}

_IPV4_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")


# ---- acceso a sub-objetos del evento ----


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def event_user(event: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(event.get("user") or event.get("user_id"))


def event_device(event: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(event.get("device") or event.get("device_id"))


def _event_type(event: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(event.get("event_type") or event.get("event_type_id"))


def event_type_code(event: dict[str, Any]) -> str:
    event_type = _event_type(event)
    code = event_type.get("code") or event_type.get("id") or ""
    return str(code).strip()


def event_type_label(event: dict[str, Any]) -> str:
    event_type = _event_type(event)
    name = str(event_type.get("name") or "").strip()
    if name:
        return name
    code = event_type_code(event)
    return EVENT_TYPE_LABELS.get(code, code)


def event_type_display(event: dict[str, Any]) -> str:
    code = event_type_code(event)
    label = event_type_label(event)
    if code and label and label != code:
        return f"{code} - {label}"
    if code:
        return f"{code} - Sin descripcion mapeada"
    return label or "Sin tipo de evento"


# ---- dispositivos ----


def device_value(device: dict[str, Any], key: str) -> str:
    value = device.get(key, "") if isinstance(device, dict) else ""
    if key == "ip" and not value and isinstance(device, dict):
        value = device.get("resolved_ip", "")
    return str(value or "").strip()


def resolve_device_ip(device: dict[str, Any]) -> str:
    """Extrae IP del campo `ip` o, si falta, del nombre del dispositivo."""
    ip = device_value(device, "ip")
    if ip:
        return ip
    name = device_value(device, "name")
    match = _IPV4_RE.search(name)
    return match.group(0) if match else ""


def device_matches(device: dict[str, Any], target: str) -> bool:
    target = str(target or "").strip().lower()
    if not target:
        return True
    did = device_value(device, "id").lower()
    name = device_value(device, "name").lower()
    ip = (device_value(device, "ip") or resolve_device_ip(device)).lower()
    return target in (did, ip) or target in name


def resolve_event_device(
    event: dict[str, Any],
    selected_device: dict[str, Any] | None = None,
    device_target: str = "",
) -> dict[str, str]:
    """Completa id/name/ip de un evento usando el dispositivo seleccionado como respaldo.

    BioStar a veces omite campos del dispositivo dentro del evento; cuando se
    monitorea un lector concreto (`--device`) usamos su inventario como relleno.
    Si `device_target` es una IPv4 y no se pudo resolver IP, se usa como último
    recurso. Función pura: no toca red ni IO.
    """
    event_dev = event_device(event)
    selected = selected_device or {}

    device_id = device_value(event_dev, "id") or device_value(selected, "id")
    device_name = device_value(event_dev, "name") or device_value(selected, "name")
    device_ip = (
        device_value(event_dev, "ip")
        or resolve_device_ip(event_dev)
        or device_value(selected, "ip")
        or resolve_device_ip(selected)
    )

    target = str(device_target or "").strip()
    if not device_ip and target.count(".") == 3:
        device_ip = target

    return {"id": device_id, "name": device_name, "ip": device_ip}


# ---- clasificación de eventos ----


def event_requires_validation(event: dict[str, Any]) -> bool:
    label = event_type_label(event).lower()
    if not label and event_type_code(event):
        return True
    return any(word in label for word in ACCESS_EVENT_KEYWORDS + DENIED_EVENT_KEYWORDS)


def event_was_denied(event: dict[str, Any]) -> bool:
    label = event_type_label(event).lower()
    return any(word in label for word in DENIED_EVENT_KEYWORDS)


def event_key(event: dict[str, Any]) -> str:
    """Llave estable para no procesar dos veces el mismo evento."""
    user = event_user(event)
    device = event_device(event)
    return "|".join(
        [
            str(event.get("id", "")),
            str(event.get("datetime", "")),
            str(user.get("user_id", "")),
            str(device.get("id", "")),
            event_type_code(event),
        ]
    )


# ---- credenciales ----


def _to_int(value: Any) -> int:
    try:
        return int(str(value or "0"))
    except (TypeError, ValueError):
        return 0


def user_credentials(user: dict[str, Any]) -> dict[str, Any]:
    """Credenciales registradas del usuario (tarjeta/huella/rostro)."""
    card = _to_int(user.get("card_count"))
    fingerprint = _to_int(user.get("fingerprint_template_count") or user.get("fingerprint_count"))
    face = _to_int(user.get("face_count")) + _to_int(user.get("visual_face_count"))
    has_card, has_fingerprint, has_face = card > 0, fingerprint > 0, face > 0
    return {
        "has_card": has_card,
        "has_fingerprint": has_fingerprint,
        "has_face": has_face,
        "event_card": False,
        "event_fingerprint": False,
        "event_face": False,
        "event_method": "",
        "card_count": card,
        "fingerprint_count": fingerprint,
        "face_count": face,
        "credential_trigger": has_card or has_fingerprint or has_face,
    }


def event_credential_flags(event: dict[str, Any]) -> dict[str, Any]:
    """Identifica qué credencial disparó el evento."""
    label = event_type_label(event).lower()
    code = event_type_code(event)
    text = f"{code} {label}"
    has_card = "card" in text or code in _CARD_CODES
    has_fingerprint = "fingerprint" in text or code in _FINGERPRINT_CODES
    has_face = "face" in text or "visual face" in text or code in _FACE_CODES
    methods = []
    if has_card:
        methods.append("CARD")
    if has_fingerprint:
        methods.append("FINGERPRINT")
    if has_face:
        methods.append("FACE")
    return {
        "event_card": has_card,
        "event_fingerprint": has_fingerprint,
        "event_face": has_face,
        "event_method": "+".join(methods),
    }


def merge_event_credentials(credentials: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Combina credenciales registradas con la que efectivamente disparó el evento."""
    merged = dict(credentials)
    flags = event_credential_flags(event)
    merged.update(flags)
    merged["has_card"] = bool(merged.get("has_card") or flags["event_card"])
    merged["has_fingerprint"] = bool(merged.get("has_fingerprint") or flags["event_fingerprint"])
    merged["has_face"] = bool(merged.get("has_face") or flags["event_face"])
    merged["credential_trigger"] = bool(
        merged.get("has_card") or merged.get("has_fingerprint") or merged.get("has_face")
    )
    return merged


# ---- fechas ----


def format_biostar_datetime(value: Any, timezone_name: str) -> str:
    """Convierte fecha BioStar (UTC/ISO) a hora local legible.

    Si la zona horaria no está disponible (Windows sin `tzdata`), devuelve el
    valor original en vez de fallar.
    """
    if not value:
        return ""
    try:
        normalized = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        local = dt.astimezone(ZoneInfo(timezone_name))
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, ZoneInfoNotFoundError):
        return str(value)
