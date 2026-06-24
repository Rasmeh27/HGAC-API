import json

import pytest
from fastapi import HTTPException

from app.api.routes.biostar_routes import latest_local_event
def test_latest_local_event_returns_monitor_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "hgac_biostar_local.json"
    expected = {
        "source": "biostar_local",
        "trigger": True,
        "permitir_paso": True,
        "event_type_code": "4867",
        "nombre": "Byron Russell",
    }
    path.write_text(json.dumps(expected), encoding="utf-8")
    monkeypatch.setenv("BIOSTAR_LOCAL_OUTPUT_PATH", str(path))

    assert latest_local_event() == expected


def test_latest_local_event_reports_monitor_not_started(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "BIOSTAR_LOCAL_OUTPUT_PATH", str(tmp_path / "missing.json")
    )

    with pytest.raises(HTTPException) as captured:
        latest_local_event()

    assert captured.value.status_code == 503
