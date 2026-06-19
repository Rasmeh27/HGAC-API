"""Contrato del motor de lectura de placa (LPR).

Aísla al resto del backend del motor concreto (OpenCV+EasyOCR hoy, otro mejor
mañana). El motor recibe una imagen BGR ya decodificada y devuelve el mejor
candidato de placa junto con su recorte. No sabe nada de cámaras, HTTP ni
evidencia: solo "imagen entra, lectura sale".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LprEngineResult:
    """Resultado crudo del motor (incluye info de depuración).

    - `best_raw_text`: mejor texto OCR elegido (sin normalizar) o `None`.
    - `best_normalized_text`: ese texto en forma canónica (A-Z0-9) o `None`.
    - `confidence`: confianza del mejor candidato, escala 0-100.
    - `plate_crop_jpeg`: recorte JPEG (con padding) realmente usado para OCR del
      mejor candidato, o `None` si no hubo lectura.
    - `candidate_count`: nº de regiones candidatas detectadas.
    - `ocr_attempt_count`: nº de pasadas OCR ejecutadas (regiones × variantes,
      más una pasada de fallback sobre el frame completo si ninguna región dio
      texto).
    - `preprocessing_variant`: variante de preprocesado que produjo el mejor
      resultado (p.ej. `clahe`, `adaptive_threshold`), o `None`.
    - `selected_roi`: ROI (sub-región del recorte) de la que salió el ganador
      (p.ej. `serial_lower`), o `None`.
    - `digit_count` / `alpha_count`: dígitos y letras del mejor candidato.
    - `candidate_rejections`: candidatos descartados (p.ej. por pocos dígitos),
      con motivo, para depuración.
    - `candidate_scores`: mejores candidatos considerados con su score/ROI/variante.

    El motor NO decide el estado final ni valida formato contra configuración;
    solo entrega el mejor candidato y su contexto. La decisión es del servicio.
    """

    best_raw_text: str | None
    best_normalized_text: str | None
    confidence: float
    plate_crop_jpeg: bytes | None
    candidate_count: int = 0
    ocr_attempt_count: int = 0
    preprocessing_variant: str | None = None
    selected_roi: str | None = None
    digit_count: int = 0
    alpha_count: int = 0
    candidate_rejections: tuple[dict, ...] = ()
    candidate_scores: tuple[dict, ...] = ()


class LprEngine(ABC):
    """Motor de lectura de placa sobre una imagen BGR."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identificador del motor (aparece en la respuesta, p.ej. `opencv_easyocr_poc`)."""

    @abstractmethod
    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        """Detecta y lee la placa más plausible en la imagen."""
