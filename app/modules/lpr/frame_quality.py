"""Calidad de frame para el pipeline LPR multiframe (placas en movimiento).

Funciones puras sobre imágenes BGR (OpenCV) para medir nitidez y brillo, decidir
si un frame es utilizable y ordenar una ráfaga por calidad. No dependen de
Settings ni de la cámara: reciben umbrales explícitos (testeable en aislamiento).

- Nitidez: varianza del Laplaciano (a mayor varianza, más bordes/enfoque).
- Brillo: media de gris (0-255).
- Un frame borroso (baja varianza) o quemado/oscuro (brillo fuera de rango) se
  descarta para no gastar OCR en frames inútiles de un vehículo en movimiento.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Referencia de saturación de la nitidez al normalizar a 0-1 (no es un umbral de
# aceptación; solo modela rendimientos decrecientes para el score de ranking).
_SHARPNESS_SATURATION = 150.0


@dataclass(frozen=True)
class FrameQuality:
    """Métricas de calidad de un frame (o de su recorte de ROI)."""

    sharpness: float
    brightness: float
    score: float  # compuesto 0-1 para ranking/depuración
    usable: bool


def _to_gray(image: np.ndarray) -> np.ndarray | None:
    if image is None or getattr(image, "size", 0) == 0:
        return None
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def compute_laplacian_sharpness(image: np.ndarray) -> float:
    """Varianza del Laplaciano (nitidez). 0 si la imagen está vacía."""
    gray = _to_gray(image)
    if gray is None:
        return 0.0
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def compute_brightness(image: np.ndarray) -> float:
    """Brillo medio (0-255). 0 si la imagen está vacía."""
    gray = _to_gray(image)
    if gray is None:
        return 0.0
    return float(gray.mean())


def is_usable_frame(
    sharpness: float,
    brightness: float,
    *,
    min_sharpness: float,
    min_brightness: float,
    max_brightness: float,
) -> bool:
    """True si el frame no está demasiado borroso ni quemado/oscuro."""
    return (
        sharpness >= min_sharpness
        and min_brightness <= brightness <= max_brightness
    )


def quality_score(
    sharpness: float,
    brightness: float,
    *,
    min_brightness: float,
    max_brightness: float,
) -> float:
    """Score compuesto 0-1: 70% nitidez (saturante) + 30% brillo centrado."""
    sharp = sharpness / (sharpness + _SHARPNESS_SATURATION) if sharpness > 0 else 0.0
    mid = (min_brightness + max_brightness) / 2.0
    span = max(1.0, (max_brightness - min_brightness) / 2.0)
    bright = max(0.0, 1.0 - abs(brightness - mid) / span)
    return round(0.7 * sharp + 0.3 * bright, 4)


def assess_quality(
    image: np.ndarray,
    *,
    min_sharpness: float,
    min_brightness: float,
    max_brightness: float,
) -> FrameQuality:
    """Mide nitidez+brillo del `image` (típicamente el recorte de ROI) y decide usabilidad."""
    sharpness = compute_laplacian_sharpness(image)
    brightness = compute_brightness(image)
    usable = is_usable_frame(
        sharpness,
        brightness,
        min_sharpness=min_sharpness,
        min_brightness=min_brightness,
        max_brightness=max_brightness,
    )
    score = quality_score(
        sharpness, brightness, min_brightness=min_brightness, max_brightness=max_brightness
    )
    return FrameQuality(
        sharpness=round(sharpness, 2),
        brightness=round(brightness, 2),
        score=score,
        usable=usable,
    )


def rank_frames(frames, key=lambda frame: frame.quality.score):
    """Ordena una lista de frames por calidad (mejor primero).

    `key` extrae el score de calidad; por defecto asume objetos con
    ``.quality.score`` (p.ej. `FrameCapture`). Estable y sin mutar la entrada.
    """
    return sorted(frames, key=key, reverse=True)
