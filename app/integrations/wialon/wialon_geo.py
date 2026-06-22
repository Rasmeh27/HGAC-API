"""Helpers geométricos y de geocercas para Wialon.

Funciones puras (sin red): cálculo de distancias, punto-en-polígono y
clasificación de geocercas. Replican la lógica de `test_wialon.py`.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable

WIALON_CLASS_LABELS = {
    2: "Unidad GPS / vehiculo (avl_unit)",
}


def ts_to_str(ts: Any) -> str:
    if not ts:
        return "Sin datos"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return str(ts)


def class_label(cls: Any) -> str:
    try:
        key = int(cls)
    except (TypeError, ValueError):
        return "Desconocido"
    return WIALON_CLASS_LABELS.get(key, "Objeto Wialon")


def distance_meters(x1: float, y1: float, x2: float, y2: float) -> float:
    mean_lat = math.radians((y1 + y2) / 2.0)
    dx = (x2 - x1) * 111320.0 * math.cos(mean_lat)
    dy = (y2 - y1) * 110540.0
    return math.sqrt(dx * dx + dy * dy)


def distance_to_segment_meters(
    x: float, y: float, ax: float, ay: float, bx: float, by: float
) -> float:
    mean_lat = math.radians((y + ay + by) / 3.0)
    scale_x = 111320.0 * math.cos(mean_lat)
    scale_y = 110540.0
    px, py = x * scale_x, y * scale_y
    a_x, a_y = ax * scale_x, ay * scale_y
    b_x, b_y = bx * scale_x, by * scale_y
    vx, vy = b_x - a_x, b_y - a_y
    length_sq = vx * vx + vy * vy
    if length_sq <= 0:
        return math.sqrt((px - a_x) ** 2 + (py - a_y) ** 2)
    ratio = ((px - a_x) * vx + (py - a_y) * vy) / length_sq
    ratio = max(0.0, min(1.0, ratio))
    nearest_x = a_x + ratio * vx
    nearest_y = a_y + ratio * vy
    return math.sqrt((px - nearest_x) ** 2 + (py - nearest_y) ** 2)


def point_in_polygon(x: float, y: float, points: list[dict[str, Any]]) -> bool:
    count = len(points)
    if count < 3:
        return False
    inside = False
    previous = count - 1
    for current in range(count):
        xi, yi = points[current]["x"], points[current]["y"]
        xj, yj = points[previous]["x"], points[previous]["y"]
        crosses = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if crosses:
            inside = not inside
        previous = current
    return inside


def zone_contains(zone: dict[str, Any], lon: float, lat: float) -> bool:
    points = zone.get("p", []) or []
    if not points:
        return False

    bounds = zone.get("b", {}) or {}
    if bounds and not (
        bounds.get("min_x", lon) <= lon <= bounds.get("max_x", lon)
        and bounds.get("min_y", lat) <= lat <= bounds.get("max_y", lat)
    ):
        return False

    zone_type = int(zone.get("t", 0) or 0)
    width = float(zone.get("w", 0) or 0)

    if zone_type == 3:
        center = points[0]
        radius = float(center.get("r", 0) or width)
        return distance_meters(lon, lat, center["x"], center["y"]) <= radius

    if zone_type == 2 and point_in_polygon(lon, lat, points):
        return True

    # Geocercas tipo línea y borde de polígono: distancia al segmento central.
    if width > 0 and len(points) >= 2:
        segment_points = list(points)
        if zone_type == 2:
            segment_points.append(points[0])
        for index in range(len(segment_points) - 1):
            start = segment_points[index]
            end = segment_points[index + 1]
            if distance_to_segment_meters(
                lon, lat, start["x"], start["y"], end["x"], end["y"]
            ) <= width:
                return True
    return False


def classify_geofences(
    zones: list[dict[str, Any]],
    lon: float,
    lat: float,
    terminal_names: Iterable[str],
    gate_keywords: Iterable[str],
) -> tuple[list[str], bool, bool]:
    """Devuelve (geocercas_que_contienen_el_punto, inside_terminal, inside_gate_zone)."""
    if not lon or not lat:
        return [], False, False
    terminal_set = {name.upper().strip() for name in terminal_names}
    keywords = [kw.upper().strip() for kw in gate_keywords]
    matches = [zone.get("n", "") for zone in zones if zone_contains(zone, lon, lat)]
    normalized = [name.upper().strip() for name in matches]
    inside_terminal = any(name in terminal_set for name in normalized)
    inside_gate_zone = any(
        any(keyword in name for keyword in keywords) for name in normalized
    )
    return matches, inside_terminal, inside_gate_zone
