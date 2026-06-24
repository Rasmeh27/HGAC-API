# HGAC - Navis API JSON ingest
# Pegar dentro del handleTimerEvent() fijo y aplicar un Tab a todo el bloque.
# Rate recomendado: 1000 ms durante pruebas.

from java.io import File
from java.text import SimpleDateFormat
from java.util import Locale

TAG_PROVIDER = "[default]"
BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1/Navis"
NAVIS_JSON = "C:\\Users\\Public\\hgac_navis.json"


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


def navis_date(value):
	if value is None or str(value).strip() == "":
		return None
	for pattern in ["dd-MMM-yy HHmm", "dd-MMM-yyyy HHmm", "yyyy-MM-dd HH:mm:ss"]:
		try:
			return SimpleDateFormat(pattern, Locale.ENGLISH).parse(str(value).strip())
		except:
			pass
	return None


def text(value):
	return "" if value is None else str(value)


def write_map(values):
	paths = []
	data = []
	for name, value in values.items():
		paths.append(BASE + "/" + name)
		data.append(value)
	return system.tag.writeBlocking(paths, data)


logger = system.util.getLogger("HGAC.Navis")
payload, file_status = read_json(NAVIS_JSON)

if file_status != "OK":
	write_map({"CommStatus": file_status, "LastUpdate": now_text()})
else:
	truck_response = payload.get("truck") or {}
	driver_response = payload.get("driver") or {}
	truck = truck_response.get("data") or {}
	driver = driver_response.get("data") or {}
	results = payload.get("results") or []

	http_statuses = []
	query_values = []
	for result in results:
		try:
			http_statuses.append(int(result.get("http_status", 0) or 0))
		except:
			pass
		value = text(result.get("value")).strip()
		if value:
			query_values.append(value)

	found = bool(truck) or bool(driver)
	success = bool(payload.get("success", False))
	truck_status = text(truck.get("status")).upper()
	driver_status = text(driver.get("status")).upper()
	truck_life = text(truck.get("life_cycle_state"))
	driver_life = text(driver.get("life_cycle_state"))
	statuses_ok = (not truck or truck_status == "OK") and (not driver or driver_status == "OK")
	lifecycles_ok = (
		(not truck or truck_life.upper() == "ACTIVE")
		and (not driver or driver_life.upper() == "ACTIVE")
	)
	authorized = found and success and statuses_ok and lifecycles_ok

	filters = []
	messages = []
	count = 0
	for response in [truck_response, driver_response]:
		value = text(response.get("filter")).strip()
		if value:
			filters.append(value)
		value = text(response.get("message")).strip()
		if value:
			messages.append(value)
		try:
			count += int(response.get("count", 0) or 0)
		except:
			pass

	write_map({
		"Trigger": True,
		"Found": found,
		"Authorized": authorized,
		"Success": success,
		"Status": truck.get("status", driver.get("status", payload.get("status", ""))),
		"HttpStatus": max(http_statuses) if http_statuses else 0,
		"Message": " | ".join(messages),
		"Filter": " | ".join(filters),
		"Count": count,
		"QueryValue": " | ".join(query_values),
		"PrimaryKey": text(truck.get("primary_key", driver.get("primary_key", ""))),
		"TruckId": text(truck.get("id")),
		"TruckLicense": text(truck.get("license")),
		"TruckLicenseState": text(truck.get("license_state")),
		"TruckLicenseExpirationDate": navis_date(truck.get("license_expiration_date")),
		"TruckBatNumber": text(truck.get("bat_number")),
		"InternalTruck": bool(truck.get("internal_truck", False)),
		"LastTrkc": text(truck.get("last_trkc", truck.get("last_trk", ""))),
		"LastTruckDriverName": text(truck.get("last_truck_driver_name")),
		"LifeCycleState": truck_life or driver_life,
		"DriverPrimaryKey": text(driver.get("primary_key")),
		"DriverName": text(driver.get("name")),
		"DriverCardId": text(driver.get("card_id")),
		"DriverLicense": text(driver.get("license")),
		"DriverCallupId": text(driver.get("callup_id")),
		"DriverLicenseState": text(driver.get("license_state")),
		"DriverInternal": bool(driver.get("internal", False)),
		# Estos campos no son expuestos por los dos endpoints actuales.
		"AppointmentNumber": "",
		"TruckVisitKey": "",
		"GateId": "",
		"LaneId": "",
		"NextStageId": "",
		"RawJson": system.util.jsonEncode(payload),
		"LastUpdate": payload.get("timestamp", now_text()),
		"CommStatus": "OK"
	})
	logger.debug("Navis actualizado: truck=%s driver=%s" % (text(truck.get("id")), text(driver.get("card_id"))))
