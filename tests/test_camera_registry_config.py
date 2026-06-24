import json

from app.modules.camera.camera_registry import CameraConfig, CameraRegistry


def test_registry_loads_rtsp_from_environment_and_roi(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "TEST_CAMERA_RTSP_URL",
        "rtsp://operator:secret@192.0.2.10:554/stream1",
    )
    path = tmp_path / "cameras.json"
    path.write_text(
        json.dumps(
            {
                "cameras": [
                    {
                        "camera_id": "GATE1-LANE1",
                        "camera_name": "Gate 1 Lane 1",
                        "source_type": "rtsp",
                        "source_env": "TEST_CAMERA_RTSP_URL",
                        "lpr_roi": {"x": 10, "y": 20, "width": 300, "height": 160},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    camera = CameraRegistry.from_json(path).get("GATE1-LANE1")

    assert camera.source == "rtsp://operator:secret@192.0.2.10:554/stream1"
    assert camera.safe_source == "rtsp://192.0.2.10:554/stream1"
    assert camera.has_lpr_roi is True
    assert (camera.roi_x, camera.roi_y, camera.roi_width, camera.roi_height) == (
        10,
        20,
        300,
        160,
    )


def test_rtsp_safe_source_never_exposes_credentials() -> None:
    camera = CameraConfig(
        camera_id="CAM-1",
        camera_name="Camera 1",
        source_type="rtsp",
        source="rtsp://user:password@198.51.100.20:554/live?token=private",
    )

    assert camera.safe_source == "rtsp://198.51.100.20:554/live"
    assert "user" not in camera.safe_source
    assert "password" not in camera.safe_source
    assert "private" not in camera.safe_source

