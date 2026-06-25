"""Detección de regiones candidatas a placa con OpenCV (PoC).

Heurística clásica: bordes + contornos, filtrando por relación de aspecto y
tamaño relativo plausibles para una placa. No reconoce texto (eso es del OCR);
solo acota dónde mirar para no correr OCR sobre todo el frame.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class PlateCandidate:
    x: int
    y: int
    width: int
    height: int

    def crop(self, image: np.ndarray) -> np.ndarray:
        return image[self.y : self.y + self.height, self.x : self.x + self.width]


class OpenCvPlateDetector:
    def __init__(
        self,
        min_aspect_ratio: float = 1.5,
        max_aspect_ratio: float = 6.5,
        min_area_ratio: float = 0.001,
        max_area_ratio: float = 0.10,
        min_fill_ratio: float = 0.20,
        max_candidates: int = 5,
    ) -> None:
        self._min_aspect_ratio = min_aspect_ratio
        self._max_aspect_ratio = max_aspect_ratio
        self._min_area_ratio = min_area_ratio
        self._max_area_ratio = max_area_ratio
        self._min_fill_ratio = min_fill_ratio
        self._max_candidates = max_candidates

    def detect(self, frame_bgr: np.ndarray) -> list[PlateCandidate]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        # Suaviza ruido conservando bordes (mejor que blur gaussiano para placas).
        gray = cv2.bilateralFilter(gray, 11, 17, 17)
        edges = cv2.Canny(gray, 30, 200)

        contours, _ = cv2.findContours(
            edges.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        frame_height, frame_width = frame_bgr.shape[:2]
        frame_area = float(frame_height * frame_width)
        scored: list[tuple[float, PlateCandidate]] = []

        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if height == 0:
                continue
            aspect_ratio = width / height
            area = float(width * height)
            if not (self._min_aspect_ratio <= aspect_ratio <= self._max_aspect_ratio):
                continue
            area_ratio = area / frame_area
            if not (self._min_area_ratio <= area_ratio <= self._max_area_ratio):
                continue
            fill_ratio = abs(float(cv2.contourArea(contour))) / area
            if fill_ratio < self._min_fill_ratio:
                continue
            perimeter = cv2.arcLength(contour, True)
            vertices = len(cv2.approxPolyDP(contour, 0.02 * perimeter, True))
            quad_bonus = 30.0 if vertices == 4 else 0.0
            aspect_score = max(0.0, 30.0 - abs(aspect_ratio - 2.0) * 12.0)
            compactness_score = fill_ratio * 100.0
            score = compactness_score + aspect_score + quad_bonus
            scored.append(
                (score, PlateCandidate(x=x, y=y, width=width, height=height))
            )

        # Mayor área primero: las placas suelen ser de los rectángulos más grandes.
        scored.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _, candidate in scored[: self._max_candidates]]
