"""Corrección OCR posicional para placas dominicanas (motor SimpleLPR).

SimpleLPR no tiene plantilla de República Dominicana; al leer con países vecinos
el OCR confunde sistemáticamente la zona de LETRAS con la de DÍGITOS (p.ej. lee
``0F00105`` en vez de ``OF00105``). Este helper genera HIPÓTESIS de corrección
por posición, sin inventar ni autocompletar caracteres:

* `raw_text` nunca se modifica;
* solo se mapean confusiones conocidas (O<->0, I<->1, ...) según la zona;
* cada hipótesis registra cuántas sustituciones requirió;
* la VALIDACIÓN de formato NO ocurre aquí: la hace el catálogo dominicano del
  backend. Este módulo solo propone candidatos trazables.

Es puro (sin IO, sin red, sin OCR) y por tanto directamente testeable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_ALNUM = re.compile(r"[^A-Z0-9]")

# Confusiones OCR cuando la posición debería ser LETRA (dígito leído como letra).
TO_LETTER: dict[str, str] = {
    "0": "O",
    "1": "I",
    "5": "S",
    "8": "B",
    "2": "Z",
    "4": "A",
    "6": "G",
}

# Confusiones OCR cuando la posición debería ser DÍGITO (letra leída como dígito).
TO_DIGIT: dict[str, str] = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "A": "4",
    "G": "6",
    "T": "7",
}

# Layouts dominicanos posibles: número de letras iniciales antes de los dígitos.
# 1 letra  -> A123456 / motocicleta (1 letra + dígitos)
# 2 letras -> OF00105 / PP123456 / EX/DD/OE/OP/OM...
_LAYOUT_LETTER_COUNTS = (1, 2)


def basic_normalize(raw_text: str | None) -> str:
    """Limpieza básica: mayúsculas y solo [A-Z0-9] (sin espacios, guiones, ruido)."""
    if not raw_text:
        return ""
    return _NON_ALNUM.sub("", raw_text.upper())


@dataclass(frozen=True)
class PlateCandidate:
    """Una hipótesis de placa derivada del texto OCR.

    - `text`: candidato normalizado (A-Z0-9).
    - `substitutions`: nº de caracteres cambiados respecto al OCR normalizado.
    - `source`: origen de la hipótesis (`raw_ocr` | `layout_1L` | `layout_2L`).
    """

    text: str
    substitutions: int
    source: str


def _apply_layout(normalized: str, letter_count: int) -> tuple[str, int]:
    """Fuerza `letter_count` letras iniciales + dígitos, corrigiendo por posición.

    Devuelve (texto_corregido, nº_sustituciones). No cambia la longitud.
    """
    if len(normalized) <= letter_count:
        return normalized, 0
    letters = "".join(TO_LETTER.get(ch, ch) for ch in normalized[:letter_count])
    digits = "".join(TO_DIGIT.get(ch, ch) for ch in normalized[letter_count:])
    corrected = letters + digits
    substitutions = sum(1 for a, b in zip(normalized, corrected) if a != b)
    return corrected, substitutions


def generate_candidates(raw_text: str | None) -> list[PlateCandidate]:
    """Genera candidatos trazables: el OCR crudo normalizado + correcciones por layout.

    El primer candidato es SIEMPRE el OCR normalizado sin tocar (0 sustituciones).
    Luego se añaden las correcciones de layout (1 y 2 letras) que difieran del crudo.
    Se deduplican por texto conservando el de MENOS sustituciones.
    """
    normalized = basic_normalize(raw_text)
    candidates: list[PlateCandidate] = [
        PlateCandidate(text=normalized, substitutions=0, source="raw_ocr")
    ]

    for letter_count in _LAYOUT_LETTER_COUNTS:
        corrected, substitutions = _apply_layout(normalized, letter_count)
        if corrected and corrected != normalized:
            candidates.append(
                PlateCandidate(
                    text=corrected,
                    substitutions=substitutions,
                    source=f"layout_{letter_count}L",
                )
            )

    return _dedupe_keep_min_substitutions(candidates)


def _dedupe_keep_min_substitutions(
    candidates: list[PlateCandidate],
) -> list[PlateCandidate]:
    best_by_text: dict[str, PlateCandidate] = {}
    for cand in candidates:
        if not cand.text:
            continue
        existing = best_by_text.get(cand.text)
        if existing is None or cand.substitutions < existing.substitutions:
            best_by_text[cand.text] = cand
    return list(best_by_text.values())
