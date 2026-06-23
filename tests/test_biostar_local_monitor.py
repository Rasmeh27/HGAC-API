"""Tests de los helpers del monitor local (scripts/monitor_biostar_local.py).

Son funciones puras (salvo `write_waiting_state`, que escribe a disco), por lo
que se prueban sin BioStar real ni red. Verifican el contrato JSON que consume
Ignition: estado WAITING_FOR_EVENT, evento diagnóstico y enriquecimiento de la
validación de acceso.
"""

import json

from app.integrations.biostar.biostar_models import (
    BioStarAccessValidation,
    BioStarCredentials,
    BioStarDeviceRef,
)
from scripts.monitor_biostar_local import (
    build_diagnostic_event_snapshot,
    build_validation_snapshot,
    write_waiting_state,
)

_DEVICE = {"id": "10", "name": "Gate Lane1", "ip": "172.17.110.49"}


def test_write_waiting_state_escribe_json_valido(tmp_path):
    target = tmp_path / "snap.json"
    payload = write_waiting_state(
        str(target), "Gate Lane1", _DEVICE, timestamp="2026-06-23 10:00:00"
    )

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert on_disk["status"] == "WAITING_FOR_EVENT"
    assert on_disk["trigger"] is False
    assert on_disk["source"] == "biostar_local"
    assert on_disk["mode"] == "local_reader_monitor"
    assert on_disk["target_device"] == "Gate Lane1"
    assert on_disk["device"] == _DEVICE


def test_write_waiting_state_sin_dispositivo(tmp_path):
    target = tmp_path / "snap.json"
    payload = write_waiting_state(str(target), "", None, timestamp="2026-06-23 10:00:00")

    assert payload["target_device"] == "ALL"
    assert payload["device"] == {"id": "", "name": "", "ip": ""}


def test_diagnostic_snapshot_trigger_false_y_estado_diagnostico():
    event = {
        "datetime": "2026-06-22T14:00:00.000Z",
        "user": {"user_id": "7", "name": "Ana"},
        "event_type": {"code": "20480", "name": "Door unlocked"},
        "device": _DEVICE,
    }
    snapshot = build_diagnostic_event_snapshot(
        event, _DEVICE, "ALL", "America/Santo_Domingo", "2026-06-23 10:00:00"
    )

    assert snapshot["trigger"] is False
    assert snapshot["estado"] == "EVENTO_DIAGNOSTICO"
    assert snapshot["permitir_paso"] is False
    assert snapshot["decision_sugerida"] == "NO_APLICA"
    assert snapshot["event_type_code"] == "20480"
    assert snapshot["nombre"] == "Ana"
    assert snapshot["device"]["ip"] == "172.17.110.49"


def test_build_validation_snapshot_enriquece_y_sobrescribe_device():
    validation = BioStarAccessValidation(
        permitir_paso=True,
        decision_sugerida="PERMITIR",
        estado="ACTIVO",
        motivo="Usuario activo con credencial registrada en BioStar",
        credentials=BioStarCredentials(has_card=True, event_method="CARD"),
        event_time="2026-06-22 10:00:00",
        event_type="1:1 authentication succeeded (Card)",
        event_type_display="4102 - 1:1 authentication succeeded (Card)",
        event_type_code="4102",
        user_id="42",
        nombre="Juan",
        # El modelo trae el device "pobre" del evento; el monitor lo reemplaza.
        device=BioStarDeviceRef(id="", name="", ip=""),
    )

    snapshot = build_validation_snapshot(
        validation, _DEVICE, "Gate Lane1", "2026-06-23 10:00:00"
    )

    assert snapshot["source"] == "biostar_local"
    assert snapshot["mode"] == "local_reader_monitor"
    assert snapshot["target_device"] == "Gate Lane1"
    assert snapshot["device"] == _DEVICE  # respaldo del dispositivo seleccionado
    assert snapshot["permitir_paso"] is True
    assert snapshot["estado"] == "ACTIVO"
    assert snapshot["credentials"]["has_card"] is True
