"""Tests del motor SimpleLPR (con un `simplelpr` falso; no requiere el paquete real).

Verifica: configuración de países (índice/nombre/inválido), error explícito si el
paquete no está, preservación del OCR crudo, corrección posicional validada contra
el catálogo dominicano, rechazo de lectura solo numérica y baja confianza cuando
hacen falta demasiadas sustituciones.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.errors import LprError
from app.integrations.lpr import simple_lpr_engine as engine_mod
from app.integrations.lpr.simple_lpr_engine import SimpleLprConfig, SimpleLprEngine
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from tests._simplelpr_fakes import build_fake_simplelpr

_CATALOG = DominicanPlatePatternCatalog()
_FRAME = np.zeros((120, 240, 3), dtype=np.uint8)


def _engine(match_specs, *, config: SimpleLprConfig | None = None) -> SimpleLprEngine:
    return SimpleLprEngine(
        config=config or SimpleLprConfig(),
        catalog=_CATALOG,
        simplelpr_module=build_fake_simplelpr(match_specs),
    )


def _scores_by_text(result) -> dict:
    return {entry["normalized_text"]: entry for entry in result.candidate_scores}


# ---- nombre / contrato ----


def test_engine_name() -> None:
    assert _engine([("L460432", 0.95, "CO")]).name == "simplelpr_rd_poc"


# ---- configuración de países ----


def test_country_activation_by_index() -> None:
    eng = _engine([("L460432", 0.95, "CO")], config=SimpleLprConfig(countries=("19", "74", "96")))
    assert eng.name == "simplelpr_rd_poc"


def test_country_activation_by_name() -> None:
    eng = _engine([("L460432", 0.95, "CO")], config=SimpleLprConfig(countries=("Colombia",)))
    assert eng.name == "simplelpr_rd_poc"


def test_invalid_country_raises_explicit_error() -> None:
    with pytest.raises(LprError):
        _engine([("L460432", 0.95, "CO")], config=SimpleLprConfig(countries=("999",)))


def test_not_installed_raises_explicit_error(monkeypatch) -> None:
    def _raise():
        raise LprError("SimpleLPR no está instalado. ...")

    monkeypatch.setattr(engine_mod, "_import_simplelpr", _raise)
    with pytest.raises(LprError):
        SimpleLprEngine(config=SimpleLprConfig(), catalog=_CATALOG)


# ---- lectura: crudo preservado + corrección trazable ----


def test_raw_ocr_preserved_in_candidate_scores() -> None:
    result = _engine([("0F00105", 0.90, "CO")]).read_plate(_FRAME)
    ocr_texts = {entry.get("ocr_text") for entry in result.candidate_scores}
    raw_entries = {entry["text"] for entry in result.candidate_scores}
    assert "0F00105" in ocr_texts          # el OCR crudo queda registrado
    assert "0F00105" in raw_entries        # como candidato raw_ocr (sin tocar)


def test_positional_correction_selected_when_valid() -> None:
    # 0F00105 -> OF00105 (OFICIAL). 1 sustitución, confianza penalizada pero alta.
    result = _engine([("0F00105", 0.90, "CO")]).read_plate(_FRAME)
    assert result.best_normalized_text == "OF00105"
    assert result.best_raw_text == "OF00105"
    assert result.confidence == pytest.approx(78.0)  # 90 - 12*1
    winner = _scores_by_text(result)["OF00105"]
    # Vino de una corrección de layout (1L o 2L empatan en 1 sustitución).
    assert winner["source"].startswith("layout_")
    assert winner["substitutions"] == 1


def test_letter_six_digits_plate_is_valid() -> None:
    result = _engine([("L460432", 0.95, "CO")]).read_plate(_FRAME)
    assert result.best_normalized_text == "L460432"
    assert _CATALOG.classify(result.best_normalized_text).is_valid is True
    assert result.confidence == pytest.approx(95.0)  # sin sustituciones


def test_two_letters_five_digits_plate_is_valid() -> None:
    result = _engine([("OF00105", 0.92, "PR")]).read_plate(_FRAME)
    assert result.best_normalized_text == "OF00105"
    assert _CATALOG.classify(result.best_normalized_text).is_valid is True


def test_numeric_only_reading_is_not_a_valid_rd_plate() -> None:
    result = _engine([("460432", 0.95, "CO")]).read_plate(_FRAME)
    # El motor entrega el mejor candidato, pero NO es una placa RD válida.
    assert _CATALOG.classify(result.best_normalized_text).is_valid is False


def test_too_many_substitutions_drop_confidence() -> None:
    # 0F0OIO5 -> OF00105 requiere 4 sustituciones (> límite=2): confianza desplomada.
    result = _engine(
        [("0F0OIO5", 0.90, "CO")],
        config=SimpleLprConfig(max_substitutions=2, substitution_penalty=12.0),
    ).read_plate(_FRAME)
    assert result.best_normalized_text == "OF00105"  # se vuelve válida...
    assert result.confidence < 55.0                   # ...pero con confianza muy baja
    winner = _scores_by_text(result)["OF00105"]
    assert winner["substitutions"] == 4
    assert winner["exceeds_substitution_limit"] is True


def test_no_matches_returns_no_plate() -> None:
    result = _engine([]).read_plate(_FRAME)
    assert result.best_raw_text is None
    assert result.best_normalized_text is None
    assert result.candidate_count == 0
