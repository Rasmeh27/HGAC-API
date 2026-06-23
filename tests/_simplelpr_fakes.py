"""Doble de prueba del paquete `simplelpr` (no se instala en CI/PoC).

Reproduce la superficie de API que usa `SimpleLprEngine`: EngineSetupParms,
SimpleLPR(engine), pesos de país por nombre o índice, createProcessor() y
processor.analyze() devolviendo candidatos con matches (text/confidence/ISO).

`build_fake_simplelpr(match_specs)` permite a cada test fijar qué "leyó" el OCR.
`match_specs`: lista de (texto, confianza_0_1, iso).
"""

from __future__ import annotations

import types
from dataclasses import dataclass, field


@dataclass
class _Match:
    text: str
    confidence: float
    countryISO: str = ""


@dataclass
class _Candidate:
    matches: list = field(default_factory=list)


class _Version:
    A = 3
    B = 6
    C = 3
    D = 0


def _default_country_codes() -> list[str]:
    codes = [f"Country{i}" for i in range(100)]
    codes[19] = "Colombia"
    codes[74] = "Puerto Rico"
    codes[96] = "Venezuela"
    return codes


def build_fake_simplelpr(match_specs, country_codes=None):
    """Construye un módulo `simplelpr` falso parametrizado por los matches OCR."""
    codes = country_codes or _default_country_codes()

    class EngineSetupParms:
        def __init__(self) -> None:
            self.cudaDeviceId = 0
            self.enableImageProcessingWithGPU = False
            self.enableClassificationWithGPU = False
            self.maxConcurrentImageProcessingOps = 0

    class _FakeProcessor:
        def __init__(self) -> None:
            self.plateRegionDetectionEnabled = False
            self.cropToPlateRegionEnabled = False

        def analyze(self, path):  # noqa: ARG002 - el path se ignora en el doble
            matches = [_Match(text=t, confidence=c, countryISO=iso) for (t, c, iso) in match_specs]
            return [_Candidate(matches=matches)]

    class _FakeEngine:
        def __init__(self, parms) -> None:  # noqa: ARG002
            self.versionNumber = _Version()
            self.numSupportedCountries = len(codes)
            self.weights: dict = {}
            self.product_key = None

        def get_countryCode(self, index):
            return codes[index]

        def set_countryWeight(self, country, weight):
            if isinstance(country, str):
                if country in codes:
                    self.weights[country] = weight
                    return
                # Nombre desconocido: el binding real lanza; fuerza el intento por índice.
                raise ValueError(f"unknown country name: {country}")
            index = int(country)
            if 0 <= index < len(codes):
                self.weights[index] = weight
                return
            raise ValueError(f"country index out of range: {index}")

        def realizeCountryWeights(self) -> None:
            pass

        def set_productKey(self, path) -> None:
            self.product_key = path

        def createProcessor(self):
            return _FakeProcessor()

    def SimpleLPR(parms):
        return _FakeEngine(parms)

    return types.SimpleNamespace(EngineSetupParms=EngineSetupParms, SimpleLPR=SimpleLPR)
