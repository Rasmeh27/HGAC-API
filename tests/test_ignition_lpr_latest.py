import json
from datetime import datetime, timezone

from app.integrations.ignition.ignition_json_writer import IgnitionJsonWriter
from app.modules.lpr.lpr_models import LprReadResponse, LprReadStatus


def test_write_lpr_latest_uses_ingestion_contract_and_replaces_previous(tmp_path):
    latest = tmp_path / "hgac_lpr.json"
    writer = IgnitionJsonWriter(tmp_path / "outbox", lpr_latest_path=latest)

    detected = LprReadResponse(
        event_id="LPR-1",
        camera_id="P1-CARRIL-2",
        camera_name="P1 - Carril 2",
        camera_ip="172.17.221.113",
        status=LprReadStatus.PLATE_DETECTED,
        plate="G737627",
        plate_normalized="G737627",
        confidence=88.1,
        source_frame_path="evidence/lpr/frames/frame.jpg",
        source_frame_url="http://localhost:8000/evidence/lpr/frames/frame.jpg",
        processing_time_ms=2000,
        detected_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        engine="opencv_easyocr_poc",
        consensus_votes=3,
        consensus_total=5,
        consensus_ratio=0.6,
    )
    writer.write_lpr_latest(detected)

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["plate"] == "G737627"
    assert payload["plate_matched"] is True
    assert payload["camera_ip"] == "172.17.221.113"
    assert payload["consensus_votes"] == 3

    rejected = detected.model_copy(
        update={
            "event_id": "LPR-2",
            "status": LprReadStatus.LOW_CONFIDENCE,
            "plate": None,
            "plate_normalized": None,
            "confidence": 40.0,
            "rejection_reason": "insufficient_consensus",
        }
    )
    writer.write_lpr_latest(rejected)

    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert payload["event_id"] == "LPR-2"
    assert payload["plate"] == ""
    assert payload["plate_matched"] is False
