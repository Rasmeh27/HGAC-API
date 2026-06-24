# Pegar dentro de def handleTimerEvent(): y aplicar Tab a todo el contenido.
# Gateway Timer Script recomendado: 1000 ms, Fixed Delay.

from java.io import File

TAG_PROVIDER = "[default]"
LANE_BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1"
BIOSTAR_ROOT = LANE_BASE + "/BioStarDevices"
BIOSTAR_UDT_TYPE = "BioStar"
BIOSTAR_JSON = "C:\\Users\\Public\\hgac_biostar_multi.json"
AUTO_CREATE_INSTANCES = True


def clean(value):
	return "" if value is None else str(value)


def read_json(path):
	try:
		if not File(path).exists():
			return None, "FILE_NOT_FOUND"
		text = system.file.readFileAsString(path)
		if text is None or len(text.strip()) == 0:
			return None, "EMPTY_FILE"
		return system.util.jsonDecode(text), "OK"
	except Exception as exc:
		return None, "ERROR: %s" % str(exc)


def ensure_folder(path, parent, name):
	if not system.tag.exists(path):
		system.tag.configure(parent, [{"name": name, "tagType": "Folder"}], "m")


def ensure_instance(device_id):
	ensure_folder(BIOSTAR_ROOT, LANE_BASE, "BioStarDevices")
	device_folder = BIOSTAR_ROOT + "/" + device_id
	ensure_folder(device_folder, BIOSTAR_ROOT, device_id)
	instance_path = device_folder + "/BioStar"
	if not system.tag.exists(instance_path):
		system.tag.configure(
			device_folder,
			[{"name": "BioStar", "tagType": "UdtInstance", "typeId": BIOSTAR_UDT_TYPE}],
			"m"
		)
	return instance_path


def write_device(instance, device, raw_json):
	paths = [
		instance + "/CommStatus", instance + "/Decision",
		instance + "/DispositivoId", instance + "/DispositivoIp",
		instance + "/DispositivoNombre", instance + "/Estado",
		instance + "/EventoCodigo", instance + "/EventoFecha",
		instance + "/EventoTexto", instance + "/LastUpdate",
		instance + "/Metodo", instance + "/Motivo",
		instance + "/Pasa", instance + "/RawJson",
		instance + "/TieneHuella", instance + "/TieneRostro",
		instance + "/TieneTarjeta", instance + "/Trigger",
		instance + "/UsuarioId", instance + "/UsuarioNombre"
	]
	values = [
		clean(device.get("comm_status")), clean(device.get("decision")),
		clean(device.get("device_id")), clean(device.get("device_ip")),
		clean(device.get("device_name")), clean(device.get("user_status")),
		clean(device.get("event_code")), clean(device.get("event_time")),
		clean(device.get("event_text")), clean(device.get("last_update")),
		clean(device.get("method")), clean(device.get("reason")),
		bool(device.get("allow_pass")), raw_json,
		bool(device.get("has_fingerprint")), bool(device.get("has_face")),
		bool(device.get("has_card")), bool(device.get("trigger")),
		clean(device.get("user_id")), clean(device.get("user_name"))
	]
	# Recomendados en el UDT nuevo. Se escriben solo si ya existen para mantener
	# compatibilidad con la definicion BioStar utilizada durante el POC.
	optional = {
		"EventSequence": int(device.get("event_sequence") or 0),
		"Online": bool(device.get("online")),
		"DeviceStatus": clean(device.get("device_status"))
	}
	for name, value in optional.items():
		path = instance + "/" + name
		if system.tag.exists(path):
			paths.append(path)
			values.append(value)
	return system.tag.writeBlocking(paths, values)


logger = system.util.getLogger("HGAC.BioStar.MultiDevice")
payload, file_status = read_json(BIOSTAR_JSON)
if file_status != "OK":
	logger.warn("No se pudo leer BioStar JSON: %s" % file_status)
else:
	devices = payload.get("devices") or []
	written = 0
	for device in devices:
		device_id = clean(device.get("device_id")).strip()
		if not device_id:
			continue
		try:
			instance = BIOSTAR_ROOT + "/" + device_id + "/BioStar"
			if AUTO_CREATE_INSTANCES:
				instance = ensure_instance(device_id)
			elif not system.tag.exists(instance):
				continue
			write_device(instance, device, system.util.jsonEncode(device))
			written += 1
		except Exception as exc:
			logger.error("Error escribiendo BioStar %s: %s" % (device_id, str(exc)))
	logger.debug("Instancias BioStar actualizadas: %s" % written)
