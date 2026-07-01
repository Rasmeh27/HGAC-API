"""Tests de la factory de motores LPR (`_build_plate_engines`).

Verifica la resolución de `LPR_ENGINE` (+ alias) y `LPR_FALLBACK_ENGINE`:
- OpenCV (default) y SimpleLPR (con `simplelpr` falso) como primario.
- Modo `auto`: SimpleLPR primario + OpenCV fallback; si SimpleLPR no está,
  degrada a OpenCV sin romper y lo reporta como UNAVAILABLE.
- `simplelpr` sin fallback y sin paquete -> `LprError` (503 controlado).
- Valor no soportado -> `ValueError`.

No carga EasyOCR (el motor OpenCV lo inicializa de forma perezosa) ni SimpleLPR real.
"""

from __future__ import annotations

import pytest

from app.api.dependencies import _build_plate_engines, _resolve_engine_alias
from app.core.config import get_settings
from app.core.errors import LprError
from app.integrations.lpr import simple_lpr_engine as engine_mod
from app.integrations.lpr.opencv_easyocr_lpr_engine import OpenCvEasyOcrLprEngine
from app.integrations.lpr.simple_lpr_engine import SimpleLprEngine
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.plate_validator import build_plate_formats, PlateValidator
from tests._simplelpr_fakes import build_fake_simplelpr


def _engines(engine: str, fallback: str | None = None):
    update = {"lpr_engine": engine}
    if fallback is not None:
        update["lpr_fallback_engine"] = fallback
    settings = get_settings().model_copy(update=update)
    formats = build_plate_formats(
        settings.lpr_plate_format_name, settings.lpr_plate_format_regex or None
    )
    validator = PlateValidator(formats=formats)
    catalog = DominicanPlatePatternCatalog()
    return _build_plate_engines(settings, formats, validator, catalog)


def _fake_simplelpr_available(monkeypatch) -> None:
    monkeypatch.setattr(engine_mod, "_import_simplelpr", lambda: build_fake_simplelpr([]))


def _simplelpr_absent(monkeypatch) -> None:
    def _raise():
        raise LprError("SimpleLPR no está instalado.")

    monkeypatch.setattr(engine_mod, "_import_simplelpr", _raise)


# --- Alias ---


def test_engine_aliases_resolve_to_canonical_names() -> None:
    assert _resolve_engine_alias("opencv_easyocr") == "opencv_easyocr_poc"
    assert _resolve_engine_alias("simplelpr") == "simplelpr_rd_poc"
    assert _resolve_engine_alias("AUTO") == "auto"


# --- OpenCV por defecto ---


def test_factory_selects_opencv_engine_by_default() -> None:
    primary, fallback, unavailable, mode = _engines("opencv_easyocr_poc")
    assert isinstance(primary, OpenCvEasyOcrLprEngine)
    assert primary.name == "opencv_easyocr_poc"
    assert fallback is None
    assert unavailable == []
    assert mode == "opencv_easyocr_poc"


def test_factory_accepts_opencv_alias() -> None:
    primary, _fb, _u, mode = _engines("opencv_easyocr")
    assert isinstance(primary, OpenCvEasyOcrLprEngine)
    assert mode == "opencv_easyocr_poc"


# --- SimpleLPR ---


def test_factory_selects_simplelpr_engine_when_configured(monkeypatch) -> None:
    _fake_simplelpr_available(monkeypatch)
    primary, _fb, unavailable, mode = _engines("simplelpr_rd_poc")
    assert isinstance(primary, SimpleLprEngine)
    assert primary.name == "simplelpr_rd_poc"
    assert mode == "simplelpr_rd_poc"
    assert unavailable == []


def test_factory_accepts_simplelpr_alias(monkeypatch) -> None:
    _fake_simplelpr_available(monkeypatch)
    primary, _fb, _u, _mode = _engines("simplelpr")
    assert isinstance(primary, SimpleLprEngine)


def test_simplelpr_without_fallback_and_absent_raises(monkeypatch) -> None:
    # Sin fallback configurado, un SimpleLPR ausente es 503 controlado (LprError).
    _simplelpr_absent(monkeypatch)
    with pytest.raises(LprError):
        _engines("simplelpr_rd_poc", fallback="")


def test_simplelpr_absent_degrades_to_fallback(monkeypatch) -> None:
    # Con fallback OpenCV, un SimpleLPR ausente degrada sin romper y lo reporta.
    _simplelpr_absent(monkeypatch)
    primary, fallback, unavailable, _mode = _engines(
        "simplelpr_rd_poc", fallback="opencv_easyocr"
    )
    assert isinstance(primary, OpenCvEasyOcrLprEngine)
    assert fallback is None
    assert len(unavailable) == 1
    assert unavailable[0]["engine"] == "simplelpr_rd_poc"
    assert unavailable[0]["status"] == "UNAVAILABLE"


# --- Modo auto ---


def test_auto_uses_simplelpr_primary_and_opencv_fallback(monkeypatch) -> None:
    _fake_simplelpr_available(monkeypatch)
    primary, fallback, unavailable, mode = _engines("auto")
    assert isinstance(primary, SimpleLprEngine)
    assert isinstance(fallback, OpenCvEasyOcrLprEngine)
    assert mode == "auto"
    assert unavailable == []


def test_auto_degrades_to_opencv_when_simplelpr_absent(monkeypatch) -> None:
    _simplelpr_absent(monkeypatch)
    primary, fallback, unavailable, mode = _engines("auto")
    assert isinstance(primary, OpenCvEasyOcrLprEngine)
    assert fallback is None
    assert mode == "auto"
    assert len(unavailable) == 1
    assert unavailable[0]["engine"] == "simplelpr_rd_poc"


# --- Valor no soportado ---


def test_factory_rejects_unsupported_engine() -> None:
    with pytest.raises(ValueError):
        _engines("motor_inexistente")
