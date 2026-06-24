#!/usr/bin/env python3
"""Inicia y supervisa los procesos continuos del PoC HGAC en una sola consola."""

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


def _specs() -> list[ProcessSpec]:
    app_python = str(VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable))
    simplelpr_python = os.getenv("SIMPLELPR_PYTHON", sys.executable)
    device = os.getenv("BIOSTAR_LOCAL_DEVICE", "172.17.110.119")
    poll = os.getenv("BIOSTAR_LOCAL_POLL_SECONDS", "1")
    return [
        ProcessSpec(
            "backend",
            [app_python, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
            _enabled("HGAC_START_BACKEND", True),
        ),
        ProcessSpec(
            "biostar",
            [app_python, str(ROOT / "scripts" / "biostar" / "test_biostar_local.py"),
             "--device", device, "--poll", poll, "--no-password-prompt"],
            _enabled("HGAC_START_BIOSTAR", True),
        ),
        ProcessSpec(
            "lpr",
            [simplelpr_python, str(ROOT / "scripts" / "lpr" / "simplelpr_rtsp_monitor.py")],
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
