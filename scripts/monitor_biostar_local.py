"""Monitor local de BioStar 2 para el PoC HGAC.

Lee eventos nuevos desde un BioStar 2 local (lector facial / tarjeta / huella)
y publica el último evento procesado como JSON en
``settings.biostar_local_output_path`` (env ``BIOSTAR_LOCAL_OUTPUT_PATH``). El
backend FastAPI expone ese archivo vía ``GET /biostar/events/latest`` para que
Ignition u otro consumidor lea el último evento.

Este script es intencionalmente *delgado*: toda la interpretación de eventos
(tipo, credenciales, validación de acceso, resolución de dispositivo) vive en
``app.integrations.biostar`` y se reutiliza aquí. NO depende de ningún módulo
externo tipo ``test_biostar``.

Uso rápido:
    python scripts/monitor_biostar_local.py --list-devices --user admin --password "PASSWORD"
    python scripts/monitor_biostar_local.py --user admin --password "PASSWORD"
    python scripts/monitor_biostar_local.py --device "Gate Lane1" --user admin --password "PASSWORD"

Detener:
    Ctrl+C
    o crear el archivo de parada (por defecto el .stop junto al JSON de salida,
    p.ej. C:\\Users\\Public\\hgac_biostar_local.stop).

Configuración (env / .env, todas opcionales con default):
    BIOSTAR_LOCAL_HOST=127.0.0.1
    BIOSTAR_LOCAL_PORT=443
    BIOSTAR_LOCAL_SCHEME=https
    BIOSTAR_LOCAL_USER=admin
    BIOSTAR_LOCAL_PASSWORD=
    BIOSTAR_LOCAL_OUTPUT_PATH=C:/Users/Public/hgac_biostar_local.json
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# El monitor se ejecuta como script suelto (`python scripts/monitor_biostar_local.py`),
# por lo que el directorio del script —no la raíz del repo— queda en sys.path[0].
# Insertamos la raíz del proyecto para poder importar el paquete `app` sin
# instalar nada (evita ModuleNotFoundError).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.errors import BioStarDeviceNotFoundError, BioStarError  # noqa: E402
from app.integrations.biostar import biostar_events  # noqa: E402
from app.integrations.biostar.biostar_factory import (  # noqa: E402
    build_biostar_local_service,
)
from app.integrations.biostar.biostar_service import BioStarService  # noqa: E402

# Defaults de operación del monitor (no son secretos ni dependen de la PC).
POLL_SECONDS = 1.0
EVENTS_LIMIT = 30
# Tras este número de llaves vistas, recortamos el set para no crecer sin límite.
_SEEN_SOFT_LIMIT = 500
_SEEN_KEEP = 250


@dataclass(frozen=True)
class LocalMonitorConfig:
    """Configuración resuelta del monitor (env + CLI + defaults)."""

    scheme: str
    host: str
    port: int
    user: str
    password: str
    output_path: str
    stop_file: str
    poll_seconds: float
    events_limit: int
    display_timezone: str

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


# --------------------------------------------------------------------------- #
# Configuración y construcción del servicio
# --------------------------------------------------------------------------- #


def load_local_monitor_config(
    args: argparse.Namespace, settings: Settings | None = None
) -> LocalMonitorConfig:
    """Fusiona settings (env/.env) con los argumentos CLI en un único config.

    El archivo de parada se deriva del JSON de salida (``*.stop``) para no
    hardcodear una segunda ruta absoluta.
    """
    settings = settings or get_settings()
    output_path = settings.biostar_local_output_path
    stop_file = str(Path(output_path).with_suffix(".stop"))
    return LocalMonitorConfig(
        scheme=args.scheme,
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        output_path=output_path,
        stop_file=stop_file,
        poll_seconds=args.poll,
        events_limit=EVENTS_LIMIT,
        display_timezone=settings.biostar_display_timezone,
    )


def build_service(
    config: LocalMonitorConfig, settings: Settings | None = None
) -> BioStarService:
    """Construye el `BioStarService` local aplicando los overrides del monitor.

    Reutiliza la factory del backend; solo sobreescribe el perfil local con los
    valores efectivos (host/puerto/usuario/clave) resueltos para esta ejecución.
    """
    settings = settings or get_settings()
    overridden = settings.model_copy(
        update={
            "biostar_local_scheme": config.scheme,
            "biostar_local_host": config.host,
            "biostar_local_port": config.port,
            "biostar_local_user": config.user,
            "biostar_local_password": config.password,
        }
    )
    return build_biostar_local_service(overridden)


# --------------------------------------------------------------------------- #
# Construcción de snapshots (puras y testeables)
# --------------------------------------------------------------------------- #


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_json_snapshot(path: str, payload: dict) -> None:
    """Escribe el snapshot JSON en disco (crea el directorio si falta)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


def build_waiting_snapshot(
    device_target: str, selected_device: dict | None, timestamp: str
) -> dict:
    """Snapshot de estado WAITING_FOR_EVENT (monitor arriba, sin eventos aún)."""
    device = selected_device or {}
    return {
        "timestamp": timestamp,
        "source": "biostar_local",
        "mode": "local_reader_monitor",
        "trigger": False,
        "target_device": device_target or "ALL",
        "device": {
            "id": biostar_events.device_value(device, "id"),
            "name": biostar_events.device_value(device, "name"),
            "ip": biostar_events.device_value(device, "ip")
            or biostar_events.resolve_device_ip(device),
        },
        "status": "WAITING_FOR_EVENT",
    }


def write_waiting_state(
    path: str,
    device_target: str,
    selected_device: dict | None,
    timestamp: str | None = None,
) -> dict:
    """Construye y persiste el snapshot WAITING_FOR_EVENT. Devuelve el payload."""
    payload = build_waiting_snapshot(device_target, selected_device, timestamp or _now_text())
    write_json_snapshot(path, payload)
    return payload


def build_diagnostic_event_snapshot(
    event: dict,
    device: dict,
    device_target: str,
    timezone_name: str,
    timestamp: str,
) -> dict:
    """Snapshot para eventos del lector NO asociados a validación de acceso."""
    user = biostar_events.event_user(event)
    return {
        "timestamp": timestamp,
        "source": "biostar_local",
        "mode": "local_reader_monitor",
        "trigger": False,
        "permitir_paso": False,
        "decision_sugerida": "NO_APLICA",
        "estado": "EVENTO_DIAGNOSTICO",
        "motivo": "Evento del dispositivo no asociado a validacion de acceso",
        "target_device": device_target or "ALL",
        "event_time": biostar_events.format_biostar_datetime(
            event.get("datetime", ""), timezone_name
        ),
        "event_time_raw": event.get("datetime", ""),
        "event_type": biostar_events.event_type_label(event),
        "event_type_display": biostar_events.event_type_display(event),
        "event_type_code": biostar_events.event_type_code(event),
        "user_id": user.get("user_id", ""),
        "nombre": user.get("name", "Desconocido"),
        "device": {
            "id": device.get("id", ""),
            "name": device.get("name", ""),
            "ip": device.get("ip", ""),
        },
    }


def build_validation_snapshot(
    validation, device: dict, device_target: str, timestamp: str
) -> dict:
    """Enriquece el resultado de `validar_evento_acceso` con metadatos del monitor.

    `validation` es un `BioStarAccessValidation` (pydantic); se serializa y se le
    añade source/mode/target_device/device (este último con el respaldo del
    dispositivo seleccionado). NO se duplica lógica de negocio.
    """
    payload = validation.model_dump()
    payload["timestamp"] = timestamp
    payload["source"] = "biostar_local"
    payload["mode"] = "local_reader_monitor"
    payload["target_device"] = device_target or "ALL"
    payload["device"] = {
        "id": device.get("id", ""),
        "name": device.get("name", ""),
        "ip": device.get("ip", ""),
    }
    return payload


def credentials_text(credentials: dict) -> str:
    return (
        f"Tarjeta:{'SI' if credentials.get('has_card') else 'NO'} "
        f"Huella:{'SI' if credentials.get('has_fingerprint') else 'NO'} "
        f"Rostro:{'SI' if credentials.get('has_face') else 'NO'} "
        f"Metodo:{credentials.get('event_method') or 'N/A'}"
    )


# --------------------------------------------------------------------------- #
# Procesamiento de eventos
# --------------------------------------------------------------------------- #


def process_local_event(
    event: dict,
    service: BioStarService,
    device_target: str,
    selected_device: dict | None,
    config: LocalMonitorConfig,
) -> dict:
    """Procesa un evento: diagnóstico vs. validación de acceso, escribe el JSON."""
    device = biostar_events.resolve_event_device(event, selected_device, device_target)
    timestamp = _now_text()

    if not biostar_events.event_requires_validation(event):
        snapshot = build_diagnostic_event_snapshot(
            event, device, device_target, config.display_timezone, timestamp
        )
        write_json_snapshot(config.output_path, snapshot)
        print(
            f"[{snapshot['event_time']}] DIAGNOSTICO | "
            f"{snapshot['event_type_display']} | "
            f"{device.get('name')} ({device.get('ip')})"
        )
        return snapshot

    validation = service.validar_evento_acceso(event)
    snapshot = build_validation_snapshot(validation, device, device_target, timestamp)
    write_json_snapshot(config.output_path, snapshot)

    decision = "PASA" if snapshot.get("permitir_paso") else "NO PASA"
    print(
        f"[{snapshot.get('event_time')}] {decision} | "
        f"{snapshot.get('nombre')} | {snapshot.get('estado')} | "
        f"{credentials_text(snapshot.get('credentials', {}))} | "
        f"{snapshot.get('event_type_display')} | "
        f"{device.get('name')} ({device.get('ip')})"
    )
    return snapshot


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Bucles principales
# --------------------------------------------------------------------------- #


def monitor_local(
    service: BioStarService,
    config: LocalMonitorConfig,
    device_target: str = "",
    process_existing: bool = False,
    include_all_events: bool = False,
) -> int:
    print("POC HGAC - BioStar local / lector facial")
    print(f"Servidor : {config.base_url}")
    print(f"Usuario  : {config.user}")
    print(f"Equipo   : {device_target or 'TODOS LOS DISPOSITIVOS LOCALES'}")
    print(f"Eventos  : {'TODOS' if include_all_events else 'SOLO VALIDACION'}")
    print(f"JSON     : {config.output_path}")
    print(f"Parada   : Ctrl+C o archivo {config.stop_file}")

    try:
        service.connect()
    except BioStarError as exc:
        print(f"\nNo se pudo conectar a BioStar local: {exc}")
        print(
            "Verifica que BioStar 2 esté corriendo en esta PC y que "
            "host/puerto/credenciales sean correctos."
        )
        return 1

    try:
        devices = service.get_devices()
        selected_device = None

        if device_target:
            try:
                selected_device = service.find_device(device_target)
            except BioStarDeviceNotFoundError:
                print(f"\nERROR: No se encontró el lector local: {device_target}")
                print("Prueba con --list-devices para ver nombre, ID e IP del dispositivo.")
                return 1
            print("\nLector local encontrado:")
            print(f"   ID     : {selected_device.get('id', '')}")
            print(f"   Nombre : {selected_device.get('name', '')}")
            print(f"   IP     : {selected_device.get('ip', '')}")
        elif devices:
            print(f"\nMonitoreando todos los dispositivos locales: {len(devices)}")

        write_waiting_state(config.output_path, device_target, selected_device)

        last_seen: set[str] = set()
        if not process_existing:
            baseline = service.get_recent_events(
                limit=config.events_limit,
                target_device=device_target or None,
                only_validation_events=not include_all_events,
            )
            last_seen.update(biostar_events.event_key(event) for event in baseline)
            print(f"\nLinea base cargada: {len(last_seen)} eventos existentes ignorados.")

        print("\nEsperando eventos nuevos: cara, tarjeta, huella o diagnóstico del lector...")

        while True:
            if os.path.exists(config.stop_file):
                print(f"\nArchivo de parada detectado: {config.stop_file}")
                _safe_remove(config.stop_file)
                break

            try:
                events = service.get_recent_events(
                    limit=config.events_limit,
                    target_device=device_target or None,
                    only_validation_events=not include_all_events,
                )
            except BioStarError as exc:
                # Hipo transitorio (sesión, red): avisamos y reintentamos.
                print(f"[WARN] Error leyendo eventos BioStar (reintentando): {exc}")
                time.sleep(config.poll_seconds)
                continue

            for event in reversed(events):
                key = biostar_events.event_key(event)
                if key in last_seen:
                    continue
                last_seen.add(key)
                process_local_event(event, service, device_target, selected_device, config)

            if len(last_seen) > _SEEN_SOFT_LIMIT:
                last_seen = set(list(last_seen)[-_SEEN_KEEP:])

            time.sleep(config.poll_seconds)

        return 0
    except KeyboardInterrupt:
        print("\nMonitor local detenido por el usuario.")
        return 0
    finally:
        service.close()


def list_local_devices(service: BioStarService) -> int:
    try:
        service.connect()
    except BioStarError as exc:
        print(f"No se pudo conectar a BioStar local: {exc}")
        return 1
    try:
        devices = service.get_devices()
        if not devices:
            print("No se encontraron dispositivos en el BioStar local.")
            return 1
        print(f"\nDispositivos locales encontrados: {len(devices)}")
        for device in devices:
            device_id = biostar_events.device_value(device, "id")
            device_ip = biostar_events.device_value(device, "ip") or biostar_events.resolve_device_ip(device)
            device_name = biostar_events.device_value(device, "name")
            print(f"   ID: {device_id:<10} IP: {device_ip:<16} Nombre: {device_name}")
        print("\nUsa --device con cualquiera de estos valores: ID, IP o parte del nombre.")
        return 0
    finally:
        service.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_arg_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor local de BioStar 2 (lector facial/tarjeta/huella) para el PoC HGAC",
    )
    parser.add_argument(
        "--scheme", default=settings.biostar_local_scheme, choices=["http", "https"], help="http o https"
    )
    parser.add_argument(
        "--host", default=settings.biostar_local_host or "127.0.0.1", help="IP/host del BioStar local"
    )
    parser.add_argument(
        "--port", type=int, default=settings.biostar_local_port or 443, help="Puerto del BioStar local"
    )
    parser.add_argument(
        "--user", default=settings.biostar_local_user or "admin", help="Usuario BioStar local"
    )
    parser.add_argument(
        "--password", default=settings.biostar_local_password, help="Password BioStar local"
    )
    parser.add_argument(
        "--no-password-prompt",
        action="store_true",
        help="No pedir password interactivo si no se pasó por argumento",
    )
    parser.add_argument(
        "--device", default="", help="Opcional: ID, IP o parte del nombre del lector local"
    )
    parser.add_argument("--poll", type=float, default=POLL_SECONDS, help="Segundos entre consultas")
    parser.add_argument(
        "--process-existing", action="store_true", help="Procesa eventos existentes al arrancar"
    )
    parser.add_argument(
        "--all-events", action="store_true", help="Incluye eventos de diagnóstico del lector"
    )
    parser.add_argument(
        "--list-devices", action="store_true", help="Solo lista dispositivos y termina"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = _build_arg_parser(settings)
    args = parser.parse_args(argv)

    if not args.password and not args.no_password_prompt:
        args.password = getpass.getpass("Password BioStar local: ")

    config = load_local_monitor_config(args, settings)

    try:
        service = build_service(config, settings)
    except BioStarError as exc:
        print(f"No se pudo inicializar el cliente BioStar local: {exc}")
        print("Define BIOSTAR_LOCAL_PASSWORD en el entorno o pasa --password.")
        return 2

    if args.list_devices:
        return list_local_devices(service)

    return monitor_local(
        service,
        config,
        device_target=args.device,
        process_existing=args.process_existing,
        include_all_events=args.all_events,
    )


if __name__ == "__main__":
    raise SystemExit(main())
