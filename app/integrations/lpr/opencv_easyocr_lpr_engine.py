"""Motor LPR PoC: OpenCV (detección + ROI del serial) + EasyOCR.

Objetivo de esta versión: priorizar el SERIAL grande de la placa y descartar el
encabezado dominicano ("REP. DOMINICANA", "REPUBLICA"...). Para ello:

- Tras recortar la placa (con padding), se enfoca el OCR en sub-ROIs del serial
  (zona inferior/central), ignorando el ~32% superior donde va el encabezado.
- Filtro duro: un candidato con menos de `min_serial_digits` (3) dígitos NUNCA
  puede ser el mejor candidato (descarta `DOMIN`, `REP`, `REPUBLICA`...).
- Scoring geométrico: además de confianza y formato, pondera nº de dígitos,
  mezcla alfanumérica, posición vertical (más abajo = serial), tamaño y ancho
  relativos del texto y cercanía al centro. Un serial gana aunque tenga menos
  confianza que un texto de encabezado.
- Modos de rendimiento (`fast`/`balanced`/`exhaustive`) que limitan regiones,
  ROIs y variantes, con early-stop al hallar un candidato fuerte. El OCR de
  frame completo queda como último recurso (solo `exhaustive`).

El motor entrega el MEJOR candidato + datos de depuración; NO decide el estado
final, NO valida contra la config de aceptación y NO infiere/autocompleta
caracteres. La decisión (formato + umbral) es del servicio.

EasyOCR se importa/inicializa de forma perezosa (no carga PyTorch al arrancar).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import product

import cv2
import numpy as np
from loguru import logger

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.integrations.lpr.opencv_plate_detector import OpenCvPlateDetector

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_OCR_AMBIGUITIES = {
    "0": "O",
    "1": "I",
    "2": "Z",
    "5": "S",
    "6": "G",
    "8": "B",
    "O": "0",
    "I": "1",
    "Z": "2",
    "S": "5",
    "G": "6",
    "B": "8",
}

# Pesos de scoring. El formato domina; los dígitos pesan fuerte para que un
# serial gane a un texto de encabezado; la geometría desempata.
_FORMAT_BONUS = 1000.0
_DIGIT_WEIGHT = 12.0
_LENGTH_BONUS = 40.0
_ALNUM_MIX_BONUS = 30.0
_HEIGHT_WEIGHT = 60.0
_WIDTH_WEIGHT = 40.0
_VPOS_WEIGHT = 50.0
_CENTER_WEIGHT = 20.0

_MAX_DEBUG_ITEMS = 8

# Fracciones del recorte de placa usadas para aislar el serial del encabezado.
_SERIAL_LOWER_TOP = 0.32
_SERIAL_MIDDLE_TOP = 0.18
_SERIAL_MIDDLE_BOTTOM = 0.95

_ALL_VARIANTS = (
    "original",
    "grayscale",
    "clahe",
    "adaptive_threshold",
    "sharpen",
    "inverted_threshold",
)


@dataclass(frozen=True)
class _ModeProfile:
    name: str
    max_regions: int
    rois: tuple[str, ...]
    variants: tuple[str, ...]
    whole_frame_fallback: bool


# Coste aproximado = max_regions × len(rois) × len(variants) pasadas OCR.
_MODE_PROFILES: dict[str, _ModeProfile] = {
    "fast": _ModeProfile(
        name="fast",
        max_regions=1,
        rois=("serial_middle",),
        variants=("original", "clahe"),
        whole_frame_fallback=False,
    ),
    "balanced": _ModeProfile(
        name="balanced",
        max_regions=1,
        rois=("serial_lower", "serial_middle"),
        variants=("original", "grayscale", "clahe", "adaptive_threshold"),
        whole_frame_fallback=False,
    ),
    "exhaustive": _ModeProfile(
        name="exhaustive",
        max_regions=3,
        rois=("serial_lower", "serial_middle", "full"),
        variants=_ALL_VARIANTS,
        whole_frame_fallback=True,
    ),
}

_DEFAULT_MODE = "balanced"


@dataclass(frozen=True)
class _Candidate:
    raw_text: str
    normalized: str
    confidence: float  # 0-100
    digit_count: int
    alpha_count: int
    y_center: float  # 0 (arriba) .. 1 (abajo), relativo a la imagen OCR'd
    x_center: float
    width_ratio: float
    height_ratio: float
    roi: str
    variant: str


class OpenCvEasyOcrLprEngine(LprEngine):
    def __init__(
        self,
        detector: OpenCvPlateDetector,
        gpu: bool = False,
        languages: tuple[str, ...] = ("en",),
        min_text_length: int = 5,
        max_text_length: int = 8,
        jpeg_quality: int = 90,
        expected_formats: tuple[str, ...] = (r"^[A-Z][0-9]{6}$",),
        expected_length: int = 7,
        upscale: int = 3,
        pad_x_ratio: float = 0.20,
        pad_y_ratio: float = 0.12,
        mode: str = _DEFAULT_MODE,
        min_serial_digits: int = 3,
        early_stop_confidence: float = 70.0,
    ) -> None:
        self._detector = detector
        self._gpu = gpu
        self._languages = list(languages)
        self._min_text_length = min_text_length
        self._max_text_length = max_text_length
        self._jpeg_quality = jpeg_quality
        self._formats = [re.compile(rx) for rx in expected_formats]
        self._expected_length = expected_length
        self._upscale = upscale
        self._pad_x_ratio = pad_x_ratio
        self._pad_y_ratio = pad_y_ratio
        self._profile = _MODE_PROFILES.get(mode, _MODE_PROFILES[_DEFAULT_MODE])
        self._min_serial_digits = min_serial_digits
        self._early_stop_confidence = early_stop_confidence
        self._reader = None  # EasyOCR Reader, inicializado perezosamente

    @property
    def name(self) -> str:
        return "opencv_easyocr_poc"

    @property
    def mode_profile(self) -> _ModeProfile:
        return self._profile

    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        reader = self._get_reader()
        profile = self._profile
        regions = self._detector.detect(frame_bgr)
        candidate_count = len(regions)
        attempts = 0
        rejections: list[dict] = []
        scores: list[dict] = []
        best: _Candidate | None = None
        best_score = float("-inf")
        best_full_crop: np.ndarray | None = None

        # 1) OCR enfocado en ROIs del serial dentro de cada región detectada.
        for region in regions[: profile.max_regions]:
            full_crop = self._pad_crop(
                frame_bgr,
                region.x,
                region.y,
                region.width,
                region.height,
                self._pad_x_ratio,
                self._pad_y_ratio,
            )
            if full_crop.size == 0:
                continue
            for roi_name in profile.rois:
                roi_image = self._extract_roi(full_crop, roi_name)
                if roi_image.size == 0:
                    continue
                for variant_name, variant_image in self._build_variants(
                    roi_image, profile.variants
                ):
                    attempts += 1
                    for bbox, raw_text, raw_conf in reader.readtext(
                        variant_image, detail=1, paragraph=False, allowlist=_ALLOWLIST
                    ):
                        cand = self._make_candidate(
                            bbox, raw_text, raw_conf, variant_image, roi_name, variant_name
                        )
                        if cand is None:
                            continue
                        if not self._is_eligible(cand):
                            _record(rejections, _rejection(cand))
                            continue
                        score = self._score(cand)
                        _record(scores, _score_entry(cand, score))
                        if score > best_score:
                            best, best_score, best_full_crop = cand, score, full_crop
                            # Early-stop: candidato fuerte (formato + confianza).
                            if self._is_strong(cand):
                                return self._result(
                                    best, best_full_crop, candidate_count,
                                    attempts, rejections, scores,
                                )

        # 2) Último recurso: OCR de frame completo (solo si nada y el modo lo permite).
        if best is None and profile.whole_frame_fallback:
            attempts += 1
            for bbox, raw_text, raw_conf in reader.readtext(
                frame_bgr, detail=1, paragraph=False, allowlist=_ALLOWLIST
            ):
                cand = self._make_candidate(
                    bbox, raw_text, raw_conf, frame_bgr, "whole_frame", "whole_frame"
                )
                if cand is None:
                    continue
                if not self._is_eligible(cand):
                    _record(rejections, _rejection(cand))
                    continue
                score = self._score(cand)
                _record(scores, _score_entry(cand, score))
                if score > best_score:
                    best, best_score = cand, score
                    best_full_crop = self._crop_bbox_padded(frame_bgr, bbox)

        return self._result(
            best, best_full_crop, candidate_count, attempts, rejections, scores
        )

    # --- selección / scoring (puro, testeable sin OCR) ---

    def _pick_best(
        self, candidates: list[_Candidate]
    ) -> tuple[_Candidate | None, float, list[dict]]:
        """Aplica el filtro de dígitos y el scoring; devuelve (mejor, score, rechazos)."""
        best: _Candidate | None = None
        best_score = float("-inf")
        rejections: list[dict] = []
        for cand in candidates:
            if not self._is_eligible(cand):
                rejections.append(_rejection(cand))
                continue
            score = self._score(cand)
            if best is None or score > best_score:
                best, best_score = cand, score
        return best, best_score, rejections

    def _is_eligible(self, cand: _Candidate) -> bool:
        # Regla mínima: un candidato de placa necesita suficientes dígitos.
        return cand.digit_count >= self._min_serial_digits

    def _is_strong(self, cand: _Candidate) -> bool:
        return (
            self._is_eligible(cand)
            and self._matches_format(cand.normalized)
            and cand.confidence >= self._early_stop_confidence
        )

    def _score(self, cand: _Candidate) -> float:
        score = cand.confidence
        if self._matches_format(cand.normalized):
            score += _FORMAT_BONUS
        score += cand.digit_count * _DIGIT_WEIGHT
        if len(cand.normalized) == self._expected_length:
            score += _LENGTH_BONUS
        if cand.alpha_count > 0 and cand.digit_count > 0:
            score += _ALNUM_MIX_BONUS
        score += cand.height_ratio * _HEIGHT_WEIGHT
        score += cand.width_ratio * _WIDTH_WEIGHT
        score += cand.y_center * _VPOS_WEIGHT  # serial está debajo del encabezado
        centrality = max(0.0, 1.0 - abs(cand.x_center - 0.5) * 2.0)
        score += centrality * _CENTER_WEIGHT
        return score

    def _matches_format(self, normalized: str) -> bool:
        return any(pattern.match(normalized) for pattern in self._formats)

    def _make_candidate(
        self, bbox, raw_text, raw_conf, image: np.ndarray, roi: str, variant: str
    ) -> _Candidate | None:
        normalized = self._clean(raw_text)
        if not self._is_length_ok(normalized):
            return None
        normalized = self._correct_ocr_ambiguities(normalized)
        height, width = image.shape[:2]
        points = np.array(bbox, dtype=np.float64)
        x_min, x_max = float(points[:, 0].min()), float(points[:, 0].max())
        y_min, y_max = float(points[:, 1].min()), float(points[:, 1].max())
        return _Candidate(
            raw_text=str(raw_text),
            normalized=normalized,
            confidence=float(raw_conf) * 100.0,
            digit_count=sum(ch.isdigit() for ch in normalized),
            alpha_count=sum(ch.isalpha() for ch in normalized),
            y_center=((y_min + y_max) / 2) / height if height else 0.5,
            x_center=((x_min + x_max) / 2) / width if width else 0.5,
            width_ratio=(x_max - x_min) / width if width else 0.0,
            height_ratio=(y_max - y_min) / height if height else 0.0,
            roi=roi,
            variant=variant,
        )

    def _clean(self, text: str) -> str:
        return _NON_ALNUM.sub("", str(text).upper())

    def _correct_ocr_ambiguities(self, text: str) -> str:
        """Corrige G/6, O/0, etc. solo si produce un formato configurado.

        Se elige la alternativa con menos sustituciones. Así ``6601421`` se
        convierte en ``G601421`` (un cambio) y no en ``GG01421`` (dos cambios).
        """
        if self._matches_format(text):
            return text
        choices = [(char, _OCR_AMBIGUITIES.get(char)) for char in text]
        options = [tuple(value for value in pair if value is not None) for pair in choices]
        matches: list[tuple[int, str]] = []
        for combination in product(*options):
            candidate = "".join(combination)
            if self._matches_format(candidate):
                changes = sum(a != b for a, b in zip(text, candidate))
                matches.append((changes, candidate))
        return min(matches)[1] if matches else text

    def _is_length_ok(self, text: str) -> bool:
        return self._min_text_length <= len(text) <= self._max_text_length

    # --- recorte, ROI y variantes ---

    @staticmethod
    def _pad_crop(
        image: np.ndarray,
        x: int,
        y: int,
        width: int,
        height: int,
        pad_x_ratio: float,
        pad_y_ratio: float,
    ) -> np.ndarray:
        """Recorta la región con padding, recortando a los límites de la imagen."""
        frame_height, frame_width = image.shape[:2]
        pad_x = int(round(width * pad_x_ratio))
        pad_y = int(round(height * pad_y_ratio))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(frame_width, x + width + pad_x)
        y1 = min(frame_height, y + height + pad_y)
        return image[y0:y1, x0:x1]

    def _extract_roi(self, crop: np.ndarray, name: str) -> np.ndarray:
        """Sub-región del recorte enfocada en el serial (ignora el encabezado)."""
        height = crop.shape[0]
        if name == "serial_lower":
            return crop[int(height * _SERIAL_LOWER_TOP) :, :]
        if name == "serial_middle":
            return crop[
                int(height * _SERIAL_MIDDLE_TOP) : int(height * _SERIAL_MIDDLE_BOTTOM),
                :,
            ]
        return crop  # "full"

    def _crop_bbox_padded(self, image: np.ndarray, bbox) -> np.ndarray | None:
        points = np.array(bbox, dtype=np.int32)
        x = int(points[:, 0].min())
        y = int(points[:, 1].min())
        width = int(points[:, 0].max()) - x
        height = int(points[:, 1].max()) - y
        if width <= 0 or height <= 0:
            # bbox degenerada: sin recorte sensato (no se guarda el frame completo).
            return None
        return self._pad_crop(
            image, x, y, width, height, self._pad_x_ratio, self._pad_y_ratio
        )

    def _build_variants(
        self, image: np.ndarray, names: tuple[str, ...]
    ) -> list[tuple[str, np.ndarray]]:
        """Genera solo las variantes pedidas (upscale + filtros), reusando intermedios."""
        upscaled = cv2.resize(
            image,
            None,
            fx=self._upscale,
            fy=self._upscale,
            interpolation=cv2.INTER_CUBIC,
        )
        cache: dict[str, np.ndarray] = {}

        def gray() -> np.ndarray:
            if "gray" not in cache:
                cache["gray"] = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
            return cache["gray"]

        def adaptive() -> np.ndarray:
            if "adaptive" not in cache:
                cache["adaptive"] = cv2.adaptiveThreshold(
                    gray(), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 5
                )
            return cache["adaptive"]

        out: list[tuple[str, np.ndarray]] = []
        for name in names:
            if name == "original":
                out.append((name, upscaled))
            elif name == "grayscale":
                out.append((name, gray()))
            elif name == "clahe":
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray())
                out.append((name, clahe))
            elif name == "adaptive_threshold":
                out.append((name, adaptive()))
            elif name == "inverted_threshold":
                out.append((name, cv2.bitwise_not(adaptive())))
            elif name == "sharpen":
                kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
                out.append((name, cv2.filter2D(upscaled, -1, kernel)))
        return out

    def _encode_jpeg(self, crop_bgr: np.ndarray | None) -> bytes | None:
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        ok, buffer = cv2.imencode(
            ".jpg", crop_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        return buffer.tobytes() if ok else None

    def _result(
        self,
        best: _Candidate | None,
        best_full_crop: np.ndarray | None,
        candidate_count: int,
        attempts: int,
        rejections: list[dict],
        scores: list[dict],
    ) -> LprEngineResult:
        top_scores = tuple(
            sorted(scores, key=lambda item: item["score"], reverse=True)[:_MAX_DEBUG_ITEMS]
        )
        top_rejections = tuple(rejections[:_MAX_DEBUG_ITEMS])

        if best is None:
            return LprEngineResult(
                best_raw_text=None,
                best_normalized_text=None,
                confidence=0.0,
                plate_crop_jpeg=None,
                candidate_count=candidate_count,
                ocr_attempt_count=attempts,
                preprocessing_variant=None,
                selected_roi=None,
                digit_count=0,
                alpha_count=0,
                candidate_rejections=top_rejections,
                candidate_scores=top_scores,
            )

        return LprEngineResult(
            best_raw_text=best.raw_text,
            best_normalized_text=best.normalized,
            confidence=round(best.confidence, 1),
            plate_crop_jpeg=self._encode_jpeg(best_full_crop),
            candidate_count=candidate_count,
            ocr_attempt_count=attempts,
            preprocessing_variant=best.variant,
            selected_roi=best.roi,
            digit_count=best.digit_count,
            alpha_count=best.alpha_count,
            candidate_rejections=top_rejections,
            candidate_scores=top_scores,
        )

    def _get_reader(self):
        if self._reader is not None:
            return self._reader
        try:
            import easyocr
        except ImportError as exc:  # pragma: no cover - depende del entorno
            raise RuntimeError(
                "EasyOCR no está instalado. Ejecuta: pip install easyocr"
            ) from exc

        logger.info(
            "LPR: inicializando EasyOCR (idiomas={}, gpu={})...",
            self._languages,
            self._gpu,
        )
        self._reader = easyocr.Reader(self._languages, gpu=self._gpu)
        return self._reader


def _rejection(cand: _Candidate) -> dict:
    return {
        "text": cand.normalized,
        "reason": "too_few_digits",
        "digit_count": cand.digit_count,
    }


def _score_entry(cand: _Candidate, score: float) -> dict:
    return {
        "text": cand.normalized,
        "score": round(score, 1),
        "roi": cand.roi,
        "variant": cand.variant,
        "digit_count": cand.digit_count,
    }


def _record(items: list[dict], item: dict) -> None:
    # Cota de memoria durante el loop; el resultado se trunca a _MAX_DEBUG_ITEMS.
    if len(items) < 64:
        items.append(item)
