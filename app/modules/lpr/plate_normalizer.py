"""Normalización de texto OCR a una placa canónica.

Conservador a propósito: para una PoC es preferible no "corregir" caracteres
(p.ej. O<->0, I<->1), porque una sustitución equivocada corrompe la placa de
forma silenciosa. Por eso solo se normaliza de forma determinista y reversible
en intención: mayúsculas y eliminación de separadores/ruido no alfanumérico.
"""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


class PlateNormalizer:
    def normalize(self, raw: str | None) -> str:
        """Devuelve la placa en mayúsculas y solo con caracteres alfanuméricos.

        Elimina espacios, guiones y cualquier signo de puntuación. Una entrada
        vacía o `None` produce cadena vacía.
        """
        if not raw:
            return ""
        return _NON_ALNUM.sub("", raw).upper()
