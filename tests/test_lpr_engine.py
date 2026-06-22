"""Tests unitarios del motor OpenCV+EasyOCR SIN invocar EasyOCR.

Construir el motor no inicializa EasyOCR (carga perezosa), así se prueban las
piezas puras (padding, ROI, variantes, filtro de dígitos, scoring geométrico y
selección) sin webcam ni modelos OCR.
"""

import numpy as np

from app.integrations.lpr.opencv_easyocr_lpr_engine import (
    _MODE_PROFILES,
    OpenCvEasyOcrLprEngine,
    _Candidate,
)
from app.integrations.lpr.opencv_plate_detector import OpenCvPlateDetector, PlateCandidate

# Formatos de prueba: ambas formas de placa dominicana.
_FORMATS = (r"^[A-Z][0-9]{6}$", r"^[A-Z]{2}[0-9]{5}$")


def _engine(mode: str = "balanced") -> OpenCvEasyOcrLprEngine:
    return OpenCvEasyOcrLprEngine(
        detector=OpenCvPlateDetector(), expected_formats=_FORMATS, mode=mode
    )


def _cand(
    normalized: str,
    confidence: float,
    *,
    y_center: float = 0.5,
    x_center: float = 0.5,
    width_ratio: float = 0.6,
    height_ratio: float = 0.4,
    roi: str = "serial_lower",
) -> _Candidate:
    return _Candidate(
        raw_text=normalized,
        normalized=normalized,
        confidence=confidence,
        digit_count=sum(c.isdigit() for c in normalized),
        alpha_count=sum(c.isalpha() for c in normalized),
        y_center=y_center,
        x_center=x_center,
        width_ratio=width_ratio,
        height_ratio=height_ratio,
        roi=roi,
        variant="grayscale",
    )


# --- A: el encabezado sin dígitos no gana; gana el serial ---


def test_header_text_without_digits_loses_to_serial() -> None:
    engine = _engine()
    candidates = [_cand("DOMIN", 98.2), _cand("OF00105", 70.0)]
    best, _score, rejections = engine._pick_best(candidates)
    assert best is not None
    assert best.normalized == "OF00105"
    assert any(r["text"] == "DOMIN" for r in rejections)


# --- B: candidatos puramente alfabéticos nunca pueden ser best ---


def test_pure_alpha_candidates_are_never_best() -> None:
    engine = _engine()
    candidates = [_cand("DOMIN", 98.2), _cand("REP", 95.0), _cand("REPUBLICA", 97.0)]
    best, _score, rejections = engine._pick_best(candidates)
    assert best is None
    assert len(rejections) == 3
    assert all(r["reason"] == "too_few_digits" for r in rejections)


def test_too_few_digits_is_not_eligible() -> None:
    engine = _engine()
    assert engine._is_eligible(_cand("OF00105", 70.0)) is True
    assert engine._is_eligible(_cand("DOMIN", 98.2)) is False
    assert engine._is_eligible(_cand("AB12", 90.0)) is False  # 2 dígitos < 3


# --- C: el serial inferior gana al texto superior (posición vertical) ---


def test_lower_serial_outscores_upper_candidate() -> None:
    engine = _engine()
    top = _cand("OF00105", 80.0, y_center=0.12)
    bottom = _cand("OF00105", 80.0, y_center=0.88)
    assert engine._score(bottom) > engine._score(top)


def test_scoring_prefers_format_match_over_higher_confidence() -> None:
    engine = _engine()
    score_valid = _cand("L460432", 60.0)  # cumple LETTER_6_DIGITS
    score_invalid = _cand("4X6043Z", 95.0)  # 4 dígitos, no cumple formato
    assert engine._score(score_valid) > engine._score(score_invalid)
    assert engine._matches_format("L460432") is True
    assert engine._matches_format("OF00105") is True
    assert engine._matches_format("460432") is False


def test_valid_plate_beats_numeric_only_even_with_lower_confidence() -> None:
    # El bug: "460432"@99.9 NO debe superar a un "L460432"@60 válido.
    engine = _engine()
    valid = _cand("L460432", 60.0)
    numeric_only = _cand("460432", 99.9)
    assert engine._score(valid) > engine._score(numeric_only)


# --- F: los modos escalan regiones/ROIs/variantes ---


def test_mode_profiles_scale_in_work() -> None:
    def passes(profile) -> int:
        return profile.max_regions * len(profile.rois) * len(profile.variants)

    fast = _MODE_PROFILES["fast"]
    balanced = _MODE_PROFILES["balanced"]
    exhaustive = _MODE_PROFILES["exhaustive"]

    assert passes(fast) < passes(balanced) < passes(exhaustive)
    # El frame completo es último recurso: solo en exhaustive.
    assert fast.whole_frame_fallback is False
    assert balanced.whole_frame_fallback is False
    assert exhaustive.whole_frame_fallback is True


def test_engine_selects_profile_by_mode() -> None:
    assert _engine(mode="fast").mode_profile.name == "fast"
    assert _engine(mode="exhaustive").mode_profile.name == "exhaustive"
    # Modo desconocido cae al por defecto (balanced), sin romper.
    assert _engine(mode="nope").mode_profile.name == "balanced"


# --- padding / ROI / variantes ---


def test_pad_crop_expands_region_by_ratios() -> None:
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    crop = OpenCvEasyOcrLprEngine._pad_crop(
        image,
        x=50,
        y=30,
        width=100,
        height=40,
        pad_left_ratio=0.2,
        pad_right_ratio=0.2,
        pad_y_ratio=0.1,
    )
    height, width = crop.shape[:2]
    assert width == 140  # 100 + 20 (izq) + 20 (der)
    assert height == 48  # 40 + 2*4


def test_pad_crop_clamps_at_image_edges() -> None:
    image = np.zeros((50, 50, 3), dtype=np.uint8)
    crop = OpenCvEasyOcrLprEngine._pad_crop(
        image,
        x=0,
        y=0,
        width=50,
        height=50,
        pad_left_ratio=0.5,
        pad_right_ratio=0.5,
        pad_y_ratio=0.5,
    )
    assert crop.shape[:2] == (50, 50)


def test_pad_crop_uses_more_left_margin() -> None:
    # Padding asimétrico: con más margen izquierdo el recorte alcanza contenido
    # más a la izquierda de la región (la letra inicial de la placa).
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    image[:, 175] = 255  # columna blanca 25 px a la izquierda de la región (x=200)
    region = {"x": 200, "y": 40, "width": 100, "height": 20}

    wide_left = OpenCvEasyOcrLprEngine._pad_crop(
        image, **region, pad_left_ratio=0.30, pad_right_ratio=0.10, pad_y_ratio=0.10
    )
    narrow_left = OpenCvEasyOcrLprEngine._pad_crop(
        image, **region, pad_left_ratio=0.10, pad_right_ratio=0.10, pad_y_ratio=0.10
    )
    # pad izquierdo 30 -> empieza en x=170 -> incluye la columna en x=175.
    assert int(wide_left[:, :, 0].max()) == 255
    # pad izquierdo 10 -> empieza en x=190 -> NO incluye x=175.
    assert int(narrow_left[:, :, 0].max()) == 0


def test_extract_serial_roi_drops_header() -> None:
    crop = np.zeros((100, 200, 3), dtype=np.uint8)
    lower = _engine()._extract_roi(crop, "serial_lower")
    # serial_lower descarta el ~32% superior (el encabezado).
    assert lower.shape[0] == 100 - int(100 * 0.32)
    assert lower.shape[1] == 200
    assert _engine()._extract_roi(crop, "full").shape[0] == 100


def test_build_variants_respects_requested_subset() -> None:
    crop = np.zeros((20, 60, 3), dtype=np.uint8)
    variants = _engine()._build_variants(crop, ("grayscale", "adaptive_threshold"))
    assert [name for name, _ in variants] == ["grayscale", "adaptive_threshold"]
    for _name, image in variants:
        assert isinstance(image, np.ndarray)
        assert image.size > 0


def test_build_variants_all_six_and_upscales() -> None:
    crop = np.zeros((20, 60, 3), dtype=np.uint8)
    variants = _engine()._build_variants(
        crop,
        (
            "original",
            "grayscale",
            "clahe",
            "adaptive_threshold",
            "sharpen",
            "inverted_threshold",
        ),
    )
    assert [name for name, _ in variants] == [
        "original",
        "grayscale",
        "clahe",
        "adaptive_threshold",
        "sharpen",
        "inverted_threshold",
    ]
    original = dict(variants)["original"]
    assert original.shape[0] == 60  # 20 * 3 (upscale)
    assert original.shape[1] == 180  # 60 * 3


def test_build_variants_includes_thin_stroke_variants() -> None:
    crop = np.zeros((20, 60, 3), dtype=np.uint8)
    variants = _engine()._build_variants(crop, ("soft_threshold", "clahe_sharpen"))
    assert [name for name, _ in variants] == ["soft_threshold", "clahe_sharpen"]
    for _name, image in variants:
        assert isinstance(image, np.ndarray)
        assert image.size > 0


def test_crop_bbox_padded_returns_region_for_valid_bbox() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    bbox = [[10, 10], [50, 10], [50, 30], [10, 30]]
    crop = _engine()._crop_bbox_padded(image, bbox)
    assert crop is not None
    assert crop.shape[0] > 20
    assert crop.shape[1] > 40


def test_crop_bbox_padded_returns_none_for_degenerate_bbox() -> None:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    bbox = [[10, 10], [10, 10], [10, 10], [10, 10]]
    assert _engine()._crop_bbox_padded(image, bbox) is None


# --- read_plate de extremo a extremo con un OCR falso (sin EasyOCR) ---
# Cubre el loop REAL de selección (filtro de dígitos + scoring + early-stop +
# candidate_rejections), no solo el helper puro _pick_best.


class _StubDetector:
    """Detector que devuelve regiones fijas (sin OpenCV de contornos)."""

    def __init__(self, candidates: list[PlateCandidate]) -> None:
        self._candidates = candidates

    def detect(self, frame_bgr) -> list[PlateCandidate]:
        return self._candidates


class _StubReader:
    """OCR falso: devuelve detecciones fijas con bbox relativa a la imagen."""

    def __init__(self, detections) -> None:
        # detections: (text, conf_0a1, y0_frac, y1_frac, x0_frac, x1_frac)
        self._detections = detections
        self.calls = 0

    def readtext(self, image, detail=1, paragraph=False, allowlist=None):
        self.calls += 1
        height, width = image.shape[:2]
        result = []
        for text, conf, y0f, y1f, x0f, x1f in self._detections:
            x0, x1 = int(width * x0f), int(width * x1f)
            y0, y1 = int(height * y0f), int(height * y1f)
            bbox = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
            result.append((bbox, text, conf))
        return result


def _stub_engine(detections, mode: str = "fast") -> OpenCvEasyOcrLprEngine:
    frame_region = PlateCandidate(x=0, y=0, width=400, height=200)
    engine = OpenCvEasyOcrLprEngine(
        detector=_StubDetector([frame_region]),
        expected_formats=_FORMATS,
        mode=mode,
    )
    engine._reader = _StubReader(detections)  # evita inicializar EasyOCR
    return engine


def test_read_plate_filters_header_and_returns_serial() -> None:
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    # Encabezado arriba (0 dígitos) + serial abajo (5 dígitos, cumple TWO_LETTERS).
    engine = _stub_engine(
        [
            ("DOMIN", 0.982, 0.05, 0.25, 0.10, 0.50),
            ("OF00105", 0.80, 0.55, 0.85, 0.10, 0.90),
        ]
    )
    result = engine.read_plate(frame)

    assert result.best_raw_text == "OF00105"
    assert result.best_normalized_text == "OF00105"
    assert result.digit_count == 5
    assert result.selected_roi == "serial_lower"
    # DOMIN fue descartado por pocos dígitos (visible en depuración).
    assert any(r["text"] == "DOMIN" for r in result.candidate_rejections)


def test_read_plate_rejects_header_only_frame() -> None:
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    engine = _stub_engine([("DOMIN", 0.982, 0.40, 0.60, 0.20, 0.80)])
    result = engine.read_plate(frame)

    # Solo texto sin dígitos -> no hay mejor candidato.
    assert result.best_raw_text is None
    assert result.best_normalized_text is None
    assert any(r["text"] == "DOMIN" for r in result.candidate_rejections)


def test_read_plate_combines_split_fragments_into_valid_plate() -> None:
    # EasyOCR devuelve la letra inicial separada del bloque de dígitos:
    # ["L", "460432"] -> combinado "L460432" (sin inventar la letra).
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    engine = _stub_engine(
        [
            ("L", 0.55, 0.45, 0.85, 0.05, 0.20),
            ("460432", 0.999, 0.45, 0.85, 0.22, 0.92),
        ]
    )
    result = engine.read_plate(frame)

    assert result.best_normalized_text == "L460432"
    assert result.digit_count == 6
    # El texto crudo conserva ambos fragmentos (no se inventó la "L").
    assert "L" in result.best_raw_text and "460432" in result.best_raw_text


def test_read_plate_does_not_invent_missing_letter() -> None:
    # Si OCR solo lee "460432" (sin fragmento de letra), NO se infiere "L460432".
    frame = np.zeros((200, 400, 3), dtype=np.uint8)
    engine = _stub_engine([("460432", 0.999, 0.45, 0.85, 0.10, 0.90)])
    result = engine.read_plate(frame)

    assert result.best_normalized_text == "460432"  # tal cual lo leyó el OCR
    # El servicio luego lo marcará FORMAT_MISMATCH; el motor no autocompleta.
