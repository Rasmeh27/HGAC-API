from pathlib import Path

from scripts.lpr.simplelpr_rtsp_monitor import (
    _atomic_write,
    adjusted_confidence,
    build_ignition_payload,
    is_recent_duplicate,
    normalize_dominican_identifier,
)


def test_normalizes_dominican_plate_without_inserting_characters() -> None:
    assert normalize_dominican_identifier("6737627") == ("G737627", "PLACA", 1)
    assert normalize_dominican_identifier("OF00105") == ("OF00105", "PLACA", 0)
    assert normalize_dominican_identifier("A62931")[1] == "DESCONOCIDO"


def test_normalizes_truck_label() -> None:
    assert normalize_dominican_identifier("F452") == ("F452", "ROTULO", 0)


def test_corrections_reduce_confidence() -> None:
    assert adjusted_confidence(0.98, 1, 0.08) == 0.90


def test_recent_duplicate_uses_type_and_identifier() -> None:
    seen = {("PLACA", "L546994"): 100.0}
    assert is_recent_duplicate(seen, "PLACA", "L546994", 102.3, 30.0)
    assert not is_recent_duplicate(seen, "ROTULO", "L546994", 102.3, 30.0)
    assert not is_recent_duplicate(seen, "PLACA", "L546994", 131.0, 30.0)


def test_ignition_payload_toggles_trigger(tmp_path: Path) -> None:
    output = tmp_path / "hgac_lpr.json"
    first = build_ignition_payload(
        identifier="G737627",
        raw_text="6737627",
        identifier_type="PLACA",
        confidence=0.90,
        camera_id="P1-CARRIL-2",
        camera_name="P1 - Carril 2",
        camera_ip="172.17.221.113",
        output_path=output,
        track_timestamp=1.5,
    )
    output.write_text(__import__("json").dumps(first), encoding="utf-8")
    second = build_ignition_payload(
        identifier="F452",
        raw_text="F452",
        identifier_type="ROTULO",
        confidence=0.88,
        camera_id="P1-CARRIL-2",
        camera_name="P1 - Carril 2",
        camera_ip="172.17.221.113",
        output_path=output,
        track_timestamp=2.0,
    )
    assert first["trigger"] is True
    assert first["plate_normalized"] == "G737627"
    assert first["confidence"] == 90.0
    assert second["trigger"] is False
    assert second["rotulo"] == "F452"
    assert second["plate"] == "G737627"
    assert second["plate_timestamp"] == first["plate_timestamp"]
    assert second["confidence"] == 90.0
    assert second["rotulo_confidence"] == 88.0
    assert second["rotulo_timestamp"] != ""
    assert second["event_sequence"] == 2


def test_atomic_write_retries_windows_file_lock(tmp_path: Path, monkeypatch) -> None:
    import scripts.lpr.simplelpr_rtsp_monitor as monitor

    output = tmp_path / "hgac_lpr.json"
    real_replace = monitor.os.replace
    attempts = 0

    def locked_twice(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("archivo ocupado por Ignition")
        return real_replace(source, destination)

    monkeypatch.setattr(monitor.os, "replace", locked_twice)
    _atomic_write(output, {"status": "PLATE_DETECTED"}, retries=3)

    assert attempts == 3
    assert output.read_text(encoding="utf-8").find("PLATE_DETECTED") >= 0
