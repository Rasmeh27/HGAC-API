#!/usr/bin/env python3
"""Inicia y supervisa los procesos continuos del PoC HGAC en una sola consola.

Lanza (y reinicia si caen) los tres procesos del PoC:

* ``backend`` -> ``uvicorn app.main:app``
* ``lpr``     -> ``scripts/lpr/simplelpr_rtsp_monitor.py`` (monitor SimpleLPR + RTSP)
* ``biostar`` -> ``scripts/monitor_biostar_local.py`` (monitor del lector local)

Toda la configuración sensible (URL RTSP, usuario/clave BioStar, IP del lector)
vive en ``.env``, NUNCA en este archivo. Cada proceso puede habilitarse o
deshabilitarse y los datos del comando se resuelven desde variables de entorno.

Variables (todas opcionales):
    HGAC_START_BACKEND=true|false        (default true)
    HGAC_START_LPR=true|false            (default true)
    HGAC_START_BIOSTAR=true|false        (default: true SOLO si BIOSTAR_LOCAL_PASSWORD
                                          está definida, para que el monitor no se
                                          quede esperando una clave por consola)
    HGAC_RESTART_DELAY_SECONDS=15
    SIMPLELPR_PYTHON                     (python con el SDK SimpleLPR; default el actual)
    BIOSTAR_LOCAL_DEVICE                 (id/nombre del lector; vacío = todos)
    BIOSTAR_LOCAL_POLL_SECONDS=1
    BIOSTAR_LOCAL_USER / BIOSTAR_LOCAL_PASSWORD  (del .env del backend)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(_path: str = ".env") -> bool:
        return False


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


@dataclass(frozen=True)
class ProcessSpec:
    name: str
    command: list[str]
    enabled: bool = True


def _enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name, "true" if default else "false")
    return value.strip().lower() in {"1", "true", "yes", "si", "on"}


def _biostar_command(app_python: str) -> list[str]:
    """Comando del monitor BioStar local, con credenciales tomadas del entorno.

    No incrusta IP ni clave: el dispositivo y las credenciales se leen de ``.env``
    (``BIOSTAR_LOCAL_*``). Si no se pasa ``--password`` y no hay clave en entorno,
    el monitor la pediría por consola; por eso el proceso solo se habilita por
    defecto cuando ``BIOSTAR_LOCAL_PASSWORD`` está definida.
    """
    command = [
        app_python,
        str(ROOT / "scripts" / "monitor_biostar_local.py"),
        "--poll",
        os.getenv("BIOSTAR_LOCAL_POLL_SECONDS", "1"),
    ]
    user = os.getenv("BIOSTAR_LOCAL_USER", "")
    password = os.getenv("BIOSTAR_LOCAL_PASSWORD", "")
    device = os.getenv("BIOSTAR_LOCAL_DEVICE", "")
    if user:
        command += ["--user", user]
    if password:
        command += ["--password", password]
    if device:
        command += ["--device", device]
    return command


def _specs() -> list[ProcessSpec]:
    app_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    simplelpr_python = os.getenv("SIMPLELPR_PYTHON", app_python)
    has_biostar_password = bool(os.getenv("BIOSTAR_LOCAL_PASSWORD", "").strip())
    return [
        ProcessSpec(
            "backend",
            [
                app_python,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            _enabled("HGAC_START_BACKEND", True),
        ),
        ProcessSpec(
            "biostar",
            _biostar_command(app_python),
            _enabled("HGAC_START_BIOSTAR", has_biostar_password),
        ),
        ProcessSpec(
            "lpr",
            [
                simplelpr_python,
                str(ROOT / "scripts" / "lpr" / "simplelpr_rtsp_monitor.py"),
            ],
            _enabled("HGAC_START_LPR", True),
        ),
    ]


def main() -> int:
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    specs = [spec for spec in _specs() if spec.enabled]
    if not specs:
        print("No hay monitores habilitados.")
        return 2
    children: dict[str, tuple[ProcessSpec, subprocess.Popen]] = {}
    restart_delay = max(3.0, float(os.getenv("HGAC_RESTART_DELAY_SECONDS", "15")))

    def start(spec: ProcessSpec) -> None:
        print(f"[Supervisor] Iniciando {spec.name}...")
        children[spec.name] = (spec, subprocess.Popen(spec.command, cwd=ROOT))

    for spec in specs:
        start(spec)
    print("[Supervisor] PoC activo. Ctrl+C detiene todos los procesos.")
    try:
        while True:
            time.sleep(2)
            for name, (spec, process) in list(children.items()):
                code = process.poll()
                if code is None:
                    continue
                print(
                    f"[Supervisor] {name} termino con codigo {code}; "
                    f"reiniciando en {restart_delay:g}s..."
                )
                time.sleep(restart_delay)
                start(spec)
    except KeyboardInterrupt:
        print("\n[Supervisor] Deteniendo procesos...")
    finally:
        for _spec, process in children.values():
            if process.poll() is None:
                process.terminate()
        for _spec, process in children.values():
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
