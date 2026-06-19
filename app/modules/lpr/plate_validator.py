"""Validación de placa: formato(s) configurable(s) + plausibilidad laxa.

El formato es ahora el criterio DURO para aceptar una lectura como
`PLATE_DETECTED`: una lectura incompleta (p.ej. "460432" sin la letra inicial)
no debe pasar solo por superar el umbral de confianza. La plausibilidad
(alfanumérica con dígito) sigue siendo un criterio laxo, usado para scoring.

Los formatos vienen por configuración (nombre + regex); por defecto el de la
PoC: `LETTER_6_DIGITS` = `^[A-Z][0-9]{6}$`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlateFormat:
    name: str
    regex: str


# Catálogo de formatos conocidos de placa dominicana (restrictivos a propósito;
# nada de `^[A-Z0-9]{7}$`, que dispara falsos positivos).
KNOWN_PLATE_FORMATS: dict[str, str] = {
    "LETTER_6_DIGITS": r"^[A-Z][0-9]{6}$",  # p.ej. L460432
    "TWO_LETTERS_5_DIGITS": r"^[A-Z]{2}[0-9]{5}$",  # p.ej. OF00105
}

DEFAULT_PLATE_FORMATS: tuple[PlateFormat, ...] = (
    PlateFormat(name="LETTER_6_DIGITS", regex=KNOWN_PLATE_FORMATS["LETTER_6_DIGITS"]),
)


def build_plate_formats(
    names: str, regex_override: str | None = None
) -> tuple[PlateFormat, ...]:
    """Resuelve una lista CSV de nombres contra el catálogo de formatos.

    - Cada nombre del catálogo usa su regex conocida.
    - Un nombre fuera del catálogo usa `regex_override` (si se da); si no, se
      ignora (no se abre la validación con regex arbitraria).
    - Si nada resuelve, cae a `DEFAULT_PLATE_FORMATS`.
    """
    formats: list[PlateFormat] = []
    for raw_name in names.split(","):
        name = raw_name.strip()
        if not name:
            continue
        if name in KNOWN_PLATE_FORMATS:
            formats.append(PlateFormat(name=name, regex=KNOWN_PLATE_FORMATS[name]))
        elif regex_override:
            formats.append(PlateFormat(name=name, regex=regex_override))
    return tuple(formats) or DEFAULT_PLATE_FORMATS


class PlateValidator:
    def __init__(
        self,
        formats: tuple[PlateFormat, ...] = DEFAULT_PLATE_FORMATS,
        min_length: int = 5,
        max_length: int = 8,
    ) -> None:
        self._formats = [(fmt.name, re.compile(fmt.regex)) for fmt in formats]
        self._min_length = min_length
        self._max_length = max_length

    def matched_format(self, plate: str | None) -> str | None:
        """Nombre del primer formato que cumple la placa, o `None`."""
        if not plate:
            return None
        for name, pattern in self._formats:
            if pattern.match(plate):
                return name
        return None

    def is_format_valid(self, plate: str | None) -> bool:
        return self.matched_format(plate) is not None

    def is_plausible(self, plate: str | None) -> bool:
        """Criterio laxo (no bloquea): longitud en rango, alfanumérico y con dígito."""
        if not plate:
            return False
        if not (self._min_length <= len(plate) <= self._max_length):
            return False
        if not plate.isalnum():
            return False
        return any(char.isdigit() for char in plate)

    @property
    def expected_format(self) -> str:
        """Etiqueta legible de los formatos esperados (para depuración)."""
        return " | ".join(name for name, _ in self._formats) or "ANY"
