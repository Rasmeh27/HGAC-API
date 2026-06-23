"""Tests del helper de corrección OCR posicional (motor SimpleLPR).

Puro, sin OCR ni red. Verifica: limpieza básica, preservación del crudo, generación
de candidatos por layout con su nº de sustituciones, y que NO se inventan caracteres.
"""

from __future__ import annotations

from app.integrations.lpr.plate_ocr_correction import (
    PlateCandidate,
    basic_normalize,
    generate_candidates,
)


def _by_text(candidates: list[PlateCandidate]) -> dict[str, PlateCandidate]:
    return {c.text: c for c in candidates}


# ---- limpieza básica ----


def test_basic_normalize_uppercases_and_strips_non_alnum() -> None:
    assert basic_normalize("of 00-105") == "OF00105"
    assert basic_normalize("a-123.456") == "A123456"


def test_basic_normalize_handles_empty_and_none() -> None:
    assert basic_normalize("") == ""
    assert basic_normalize(None) == ""


# ---- el crudo siempre se conserva como candidato 0-sustituciones ----


def test_raw_ocr_candidate_is_preserved_untouched() -> None:
    candidates = generate_candidates("0F00105")
    raw = next(c for c in candidates if c.source == "raw_ocr")
    assert raw.text == "0F00105"
    assert raw.substitutions == 0


def test_clean_reading_yields_single_candidate_zero_substitutions() -> None:
    # Una placa ya correcta no genera correcciones distintas del crudo.
    candidates = generate_candidates("OF00105")
    texts = _by_text(candidates)
    assert "OF00105" in texts
    assert texts["OF00105"].substitutions == 0


# ---- correcciones por posición trazables (sin inventar caracteres) ----


def test_positional_correction_two_letter_layout() -> None:
    # 0F00105 -> OF00105 corrigiendo el dígito inicial 0 a la letra O (1 sustitución).
    candidates = generate_candidates("0F00105")
    texts = _by_text(candidates)
    assert "OF00105" in texts
    assert texts["OF00105"].substitutions == 1


def test_positional_correction_one_letter_layout() -> None:
    # L46O432 -> L460432 corrigiendo la O de la zona numérica a 0 (1 sustitución).
    candidates = generate_candidates("L46O432")
    texts = _by_text(candidates)
    assert "L460432" in texts
    assert texts["L460432"].substitutions == 1


def test_correction_never_changes_length() -> None:
    # No se autocompleta ni se eliminan caracteres: misma longitud que el crudo limpio.
    candidates = generate_candidates("0F00105")
    assert all(len(c.text) == len("0F00105") for c in candidates)


def test_many_substitutions_are_counted() -> None:
    # 0F0OIO5 -> OF00105 requiere 4 sustituciones (trazable, no silencioso).
    candidates = generate_candidates("0F0OIO5")
    texts = _by_text(candidates)
    assert "OF00105" in texts
    assert texts["OF00105"].substitutions == 4
