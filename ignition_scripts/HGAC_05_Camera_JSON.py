# HGAC - Camera RTSP JSON ingest
# Pegar dentro del handleTimerEvent() fijo y aplicar un Tab a todo el bloque.
# Rate recomendado: 1000 ms durante pruebas.

from java.io import File

TAG_PROVIDER = "[default]"
BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1/Camera"
CAMERA_JSON = "C:\\Users\\Public\\hgac_camera.json"


def now_text():
	return system.date.format(system.date.now(), "yyyy-MM-dd HH:mm:ss")


def read_json(path):
	if not File(path).exists():
		return None, "FILE_NOT_FOUND"
	try:
		text = system.file.readFileAsString(path)
		if text is None or len(text.strip()) == 0:
			return None, "EMPTY_FILE"
		return system.util.jsonDecode(text), "OK"
	except Exception as exc:
		return None, "ERROR: %s" % str(exc)


data, status = read_json(CAMERA_JSON)
if status == "OK":
	paths = [
		BASE + "/TriggerRecord",
		BASE + "/Recording",
		BASE + "/RecordStatus",
		BASE + "/CameraId",
		BASE + "/CameraName",
		BASE + "/CameraIp",
		BASE + "/RtspUrl",
		BASE + "/DurationSeconds",
		BASE + "/Resolution",
		BASE + "/Fps",
		BASE + "/LastClipPath",
		BASE + "/LastFramePath",
		BASE + "/LastUpdate"
	]
	values = [
		bool(data.get("trigger_record", False)),
		bool(data.get("recording", False)),
		data.get("record_status", ""),
		data.get("camera_id", ""),
		data.get("camera_name", ""),
		data.get("camera_ip", ""),
		data.get("rtsp_url", ""),
		int(data.get("duration_seconds", 0) or 0),
		data.get("resolution", ""),
		float(data.get("fps", 0.0) or 0.0),
		data.get("last_clip_path", ""),
		data.get("last_frame_path", ""),
		data.get("timestamp", now_text())
	]
	system.tag.writeBlocking(paths, values)
else:
	system.util.getLogger("HGAC.Camera").warn("Camera JSON: %s" % status)
