# HGAC - Wialon multi-unit ingest
# Pegar este contenido dentro del handleTimerEvent() fijo de un Gateway Timer
# Script y aplicar una sangria (Tab) a todo el bloque.
# Rate recomendado: 5000 ms.

from java.io import File

TAG_PROVIDER = "[default]"
LANE_BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1"
WIALON_ROOT = LANE_BASE + "/WialonUnits"
WIALON_UDT_TYPE = "Wialon"
WIALON_JSON = "C:\\Users\\Public\\hgac_wialon.json"
AUTO_CREATE_INSTANCES = True


def now_text():
	return system.date.format(system.date.now(), "yyyy-MM-dd HH:mm:ss")


def clean(value, default=""):
	if value is None:
		return default
	return value


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


def ensure_folder(path, parent, name):
	if not system.tag.exists(path):
		system.tag.configure(
			parent,
			[{"name": name, "tagType": "Folder"}],
			"m"
		)


def ensure_instance(unit_id):
	ensure_folder(WIALON_ROOT, LANE_BASE, "WialonUnits")
	unit_folder = WIALON_ROOT + "/" + unit_id
	ensure_folder(unit_folder, WIALON_ROOT, unit_id)

	instance_path = unit_folder + "/Wialon"
	if not system.tag.exists(instance_path):
		system.tag.configure(
			unit_folder,
			[{
				"name": "Wialon",
				"tagType": "UdtInstance",
				"typeId": WIALON_UDT_TYPE
			}],
			"m"
		)
	return instance_path


def write_unit(instance_path, unit, source_timestamp):
	paths = [
		instance_path + "/UnitId",
		instance_path + "/UnitName",
		instance_path + "/Latitude",
		instance_path + "/Longitude",
		instance_path + "/SpeedKph",
		instance_path + "/Course",
		instance_path + "/Altitude",
		instance_path + "/Satellites",
		instance_path + "/LastGpsTime",
		instance_path + "/Online",
		instance_path + "/GeofenceName",
		instance_path + "/InsideTerminal",
		instance_path + "/InsideGateZone",
		instance_path + "/VehiclePlate",
		instance_path + "/RawJson",
		instance_path + "/LastUpdate",
		instance_path + "/CommStatus"
	]

	values = [
		str(clean(unit.get("id"))),
		clean(unit.get("nombre")),
		float(clean(unit.get("lat"), 0.0) or 0.0),
		float(clean(unit.get("lon"), 0.0) or 0.0),
		float(clean(unit.get("velocidad"), 0.0) or 0.0),
		float(clean(unit.get("rumbo"), 0.0) or 0.0),
		float(clean(unit.get("altitud"), 0.0) or 0.0),
		int(clean(unit.get("satelites"), 0) or 0),
		clean(unit.get("ultimo_reporte")),
		bool(clean(unit.get("online"), False)),
		clean(unit.get("geofence_name")),
		bool(clean(unit.get("inside_terminal"), False)),
		bool(clean(unit.get("inside_gate_zone"), False)),
		clean(unit.get("vehicle_plate")),
		system.util.jsonEncode(unit),
		source_timestamp or now_text(),
		"OK"
	]

	return system.tag.writeBlocking(paths, values)


logger = system.util.getLogger("HGAC.Wialon.MultiUnit")
data, status = read_json(WIALON_JSON)

if status != "OK":
	logger.warn("No se pudo leer Wialon JSON: %s" % status)
else:
	units = data.get("unidades") or []
	source_timestamp = data.get("timestamp") or now_text()
	written = 0
	errors = []

	for unit in units:
		unit_id = str(clean(unit.get("id"))).strip()
		if not unit_id:
			continue

		try:
			instance_path = WIALON_ROOT + "/" + unit_id + "/Wialon"
			if AUTO_CREATE_INSTANCES:
				instance_path = ensure_instance(unit_id)
			elif not system.tag.exists(instance_path):
				continue

			write_unit(instance_path, unit, source_timestamp)
			written += 1
		except Exception as exc:
			errors.append("%s: %s" % (unit_id, str(exc)))

	if errors:
		logger.warn("Wialon: %d unidades escritas; errores: %s" % (written, " | ".join(errors[:5])))
	else:
		logger.debug("Wialon: %d unidades actualizadas" % written)
