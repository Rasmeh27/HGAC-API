"""Motor LPR PoC: OpenCV (detección + ROI del serial) + EasyOCR.

Objetivos de esta versión, además de priorizar el SERIAL grande y descartar el
encabezado dominicano:

- **Combinación de fragmentos OCR**: si EasyOCR devuelve la letra inicial por
  separado (p.ej. `["L", "460432"]`), se ordenan los fragmentos por X y se
  evalúa también el texto combinado (`L460432`), para no perder la letra. NO se
  autocompleta ni se infiere ninguna letra: solo se concatena lo que el OCR ya
  leyó.
- **Padding asimétrico** (más margen izquierdo) para no cortar la letra inicial.
- **Filtro duro**: un candidato con menos de `min_serial_digits` (3) dígitos
  nunca puede ser el mejor (descarta `DOMIN`, `REP`, `REPUBLICA`...).
- **Scoring**: el formato DOMINA; se penalizan candidatos solo numéricos cuando
  el formato esperado lleva letra. Un `L460432` válido con menos confianza gana
  a un `460432` inválido con más confianza.
- **Modos** (`fast`/`balanced`/`exhaustive`) con early-stop al hallar candidato
  fuerte. El OCR de frame completo es último recurso (solo `exhaustive`, y solo
  si no hay candidato o el mejor no cumple formato).

El motor entrega el MEJOR candidato + datos de depuración; NO decide el estado
final, NO valida contra la config de aceptación y NO infiere caracteres. La
decisión (formato + umbral) es del servicio.

EasyOCR se importa/inicializa de forma perezosa (no carga PyTorch al arrancar).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.integrations.lpr.opencv_plate_detector import OpenCvPlateDetector

_NON_ALNUM = re.compile(r"[^A-Z0-9]")
_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

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
# Penalización a candidatos SOLO numéricos: los formatos esperados llevan letra,
# así que "460432" no debe competir con un alfanumérico válido.
_NUMERIC_ONLY_PENALTY = 200.0

_MAX_DEBUG_ITEMS = 8
# Cota de fragmentos a combinar (evita explosión combinatoria en ruido OCR).
_MAX_FRAGMENTS = 8

# Fracciones del recorte de placa usadas para aislar el serial del encabezado.
_SERIAL_LOWER_TOP = 0.32
_SERIAL_MIDDLE_TOP = 0.18
_SERIAL_MIDDLE_BOTTOM = 0.95

_ALL_VARIANTS = (
    "original",
    "grayscale",
    "clahe",
    "adaptive_threshold",
    "soft_threshold",
    "clahe_sharpen",
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
# El early-stop suele cortar mucho antes cuando hay una placa válida.
_MODE_PROFILES: dict[str, _ModeProfile] = {
    "fast": _ModeProfile(
        name="fast",
        max_regions=1,
        rois=("serial_lower",),
        variants=("grayscale", "adaptive_threshold"),
        whole_frame_fallback=False,
    ),
    "balanced": _ModeProfile(
        name="balanced",
        max_regions=1,
        rois=("serial_lower", "serial_middle"),
        variants=("grayscale", "adaptive_threshold", "soft_threshold"),
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
class _Fragment:
    cleaned: str
    raw: str
    confidence: float  # 0-100
    x_min: float
    y_min: float
    x_max: float
    y_max: float


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
        pad_left_ratio: float = 0.35,
        pad_right_ratio: float = 0.15,
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
        self._pad_left_ratio = pad_left_ratio
        self._pad_right_ratio = pad_right_ratio
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
                self._pad_left_ratio,
                self._pad_right_ratio,
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
                    detections = reader.readtext(
                        variant_image, detail=1, paragraph=False, allowlist=_ALLOWLIST
                    )
                    for cand, _bbox in self._candidates_from_detections(
                        detections, variant_image, roi_name, variant_name
                    ):
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

        # 2) Último recurso (solo si el modo lo permite): OCR de frame completo
        #    cuando no hay candidato o el mejor no cumple formato.
        needs_fallback = best is None or not self._matches_format(best.normalized)
        if needs_fallback and profile.whole_frame_fallback:
            attempts += 1
            detections = reader.readtext(
                frame_bgr, detail=1, paragraph=False, allowlist=_ALLOWLIST
            )
            for cand, (x0, y0, x1, y1) in self._candidates_from_detections(
                detections, frame_bgr, "whole_frame", "whole_frame"
            ):
                if not self._is_eligible(cand):
                    _record(rejections, _rejection(cand))
                    continue
                score = self._score(cand)
                _record(scores, _score_entry(cand, score))
                if score > best_score:
                    best, best_score = cand, score
                    bbox = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                    best_full_crop = self._crop_bbox_padded(frame_bgr, bbox)

        return self._result(
            best, best_full_crop, candidate_count, attempts, rejections, scores
        )

    # --- construcción de candidatos (individuales + combinados) ---

    def _candidates_from_detections(
        self, detections, image: np.ndarray, roi: str, variant: str
    ) -> list[tuple[_Candidate, tuple[float, float, float, float]]]:
        """Candidatos individuales y combinados a partir de las detecciones OCR.

        La combinación ordena los fragmentos por X y concatena ventanas
        contiguas (p.ej. `L` + `460432` -> `L460432`), sin inventar caracteres.
        """
        height, width = image.shape[:2]
        fragments: list[_Fragment] = []
        for bbox, raw_text, raw_conf in detections:
            cleaned = self._clean(raw_text)
            if not cleaned:
                continue
            points = np.array(bbox, dtype=np.float64)
            fragments.append(
                _Fragment(
                    cleaned=cleaned,
                    raw=str(raw_text),
                    confidence=float(raw_conf) * 100.0,
                    x_min=float(points[:, 0].min()),
                    y_min=float(points[:, 1].min()),
                    x_max=float(points[:, 0].max()),
                    y_max=float(points[:, 1].max()),
                )
            )

        out: list[tuple[_Candidate, tuple[float, float, float, float]]] = []

        # Individuales.
        for fragment in fragments:
            if self._is_length_ok(fragment.cleaned):
                out.append(self._candidate_from_fragments([fragment], width, height, roi, variant))

        # Combinados: ventanas contiguas en orden de X.
        if len(fragments) >= 2:
            ordered = sorted(fragments, key=lambda fr: fr.x_min)[:_MAX_FRAGMENTS]
            count = len(ordered)
            for i in range(count):
                for j in range(i + 1, count):
                    window = ordered[i : j + 1]
                    combined = "".join(fr.cleaned for fr in window)
                    if self._is_length_ok(combined):
                        out.append(
                            self._candidate_from_fragments(window, width, height, roi, variant)
                        )
        return out

    def _candidate_from_fragments(
        self, fragments: list[_Fragment], image_w: int, image_h: int, roi: str, variant: str
    ) -> tuple[_Candidate, tuple[float, float, float, float]]:
        normalized = "".join(fr.cleaned for fr in fragments)
        raw = " ".join(fr.raw for fr in fragments)
        # Confianza ponderada por longitud: el grueso del serial pesa más que un
        # fragmento corto (p.ej. la letra inicial) de baja confianza.
        total_len = sum(len(fr.cleaned) for fr in fragments) or 1
        confidence = sum(len(fr.cleaned) * fr.confidence for fr in fragments) / total_len
        x_min = min(fr.x_min for fr in fragments)
        y_min = min(fr.y_min for fr in fragments)
        x_max = max(fr.x_max for fr in fragments)
        y_max = max(fr.y_max for fr in fragments)
        candidate = _Candidate(
            raw_text=raw,
            normalized=normalized,
            confidence=confidence,
            digit_count=sum(ch.isdigit() for ch in normalized),
            alpha_count=sum(ch.isalpha() for ch in normalized),
            y_center=((y_min + y_max) / 2) / image_h if image_h else 0.5,
            x_center=((x_min + x_max) / 2) / image_w if image_w else 0.5,
            width_ratio=(x_max - x_min) / image_w if image_w else 0.0,
            height_ratio=(y_max - y_min) / image_h if image_h else 0.0,
            roi=roi,
            variant=variant,
        )
        return candidate, (x_min, y_min, x_max, y_max)

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
        if cand.alpha_count == 0:
            score -= _NUMERIC_ONLY_PENALTY
        score += cand.height_ratio * _HEIGHT_WEIGHT
        score += cand.width_ratio * _WIDTH_WEIGHT
        score += cand.y_center * _VPOS_WEIGHT  # serial está debajo del encabezado
        centrality = max(0.0, 1.0 - abs(cand.x_center - 0.5) * 2.0)
        score += centrality * _CENTER_WEIGHT
        return score

    def _matches_format(self, normalized: str) -> bool:
        return any(pattern.match(normalized) for pattern in self._formats)

    def _clean(self, text: str) -> str:
        return _NON_ALNUM.sub("", str(text).upper())

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
        pad_left_ratio: float,
        pad_right_ratio: float,
        pad_y_ratio: float,
    ) -> np.ndarray:
        """Recorta con padding ASIMÉTRICO (más a la izquierda), recortado a bordes."""
        frame_height, frame_width = image.shape[:2]
        pad_left = int(round(width * pad_left_ratio))
        pad_right = int(round(width * pad_right_ratio))
        pad_y = int(round(height * pad_y_ratio))
        x0 = max(0, x - pad_left)
        y0 = max(0, y - pad_y)
        x1 = min(frame_width, x + width + pad_right)
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
            image,
            x,
            y,
            width,
            height,
            self._pad_left_ratio,
            self._pad_right_ratio,
            self._pad_y_ratio,
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

        def clahe() -> np.ndarray:
            if "clahe" not in cache:
                cache["clahe"] = cv2.createCLAHE(
                    clipLimit=2.0, tileGridSize=(8, 8)
                ).apply(gray())
            return cache["clahe"]

        sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        out: list[tuple[str, np.ndarray]] = []
        for name in names:
            if name == "original":
                out.append((name, upscaled))
            elif name == "grayscale":
                out.append((name, gray()))
            elif name == "clahe":
                out.append((name, clahe()))
            elif name == "adaptive_threshold":
                out.append((name, adaptive()))
            elif name == "soft_threshold":
                # Umbral global Otsu: menos agresivo, conserva trazos finos (la "L").
                _, otsu = cv2.threshold(
                    gray(), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
                )
                out.append((name, otsu))
            elif name == "clahe_sharpen":
                out.append((name, cv2.filter2D(clahe(), -1, sharpen_kernel)))
            elif name == "inverted_threshold":
                out.append((name, cv2.bitwise_not(adaptive())))
            elif name == "sharpen":
                out.append((name, cv2.filter2D(upscaled, -1, sharpen_kernel)))
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
