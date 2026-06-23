"""Configuración compartida de pytest.

Garantiza que la raíz del repositorio esté en `sys.path` para poder importar
tanto el paquete `app` como los scripts sueltos de `scripts/` (p.ej. el monitor
local de BioStar) durante las pruebas.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
