"""Tests de la factory de motores LPR (`_build_lpr_engine`).

Verifica la selección por `LPR_ENGINE`: motor OpenCV (default), motor SimpleLPR
(con `simplelpr` falso o ausente) y error claro ante un valor no soportado. No
carga EasyOCR (el motor OpenCV lo inicializa de forma perezosa) ni SimpleLPR real.
"""

from __future__ import annotations

import pytest

from app.api.dependencies import _build_lpr_engine
from app.core.config import get_settings
from app.core.errors import LprError
from app.integrations.lpr import simple_lpr_engine as engine_mod
from app.integrations.lpr.opencv_easyocr_lpr_engine import OpenCvEasyOcrLprEngine
from app.integrations.lpr.simple_lpr_engine import SimpleLprEngine
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.plate_validator import build_plate_formats, PlateValidator
from tests._simplelpr_fakes import build_fake_simplelpr


def _settings(engine: str):
    return get_settings().model_copy(update={"lpr_engine": engine})


def _build(engine: str):
    settings = _settings(engine)
    formats = build_plate_formats(
        settings.lpr_plate_format_name, settings.lpr_plate_format_regex or None
    )
    validator = PlateValidator(formats=formats)
    catalog = DominicanPlatePatternCatalog()
    return _build_lpr_engine(settings, formats, validator, catalog)


def test_factory_selects_opencv_engine_by_default() -> None:
    engine = _build("opencv_easyocr_poc")
    assert isinstance(engine, OpenCvEasyOcrLprEngine)
    assert engine.name == "opencv_easyocr_poc"


def test_factory_selects_simplelpr_engine_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        engine_mod, "_import_simplelpr", lambda: build_fake_simplelpr([])
    )
    engine = _build("simplelpr_rd_poc")
    assert isinstance(engine, SimpleLprEngine)
    assert engine.name == "simplelpr_rd_poc"


def test_factory_raises_when_simplelpr_not_installed(monkeypatch) -> None:
    def _raise():
        raise LprError(
            "SimpleLPR no está instalado. Instala la dependencia o cambia LPR_ENGINE."
        )

    monkeypatch.setattr(engine_mod, "_import_simplelpr", _raise)
    with pytest.raises(LprError):
        _build("simplelpr_rd_poc")


def test_factory_rejects_unsupported_engine() -> None:
    with pytest.raises(ValueError):
        _build("motor_inexistente")
