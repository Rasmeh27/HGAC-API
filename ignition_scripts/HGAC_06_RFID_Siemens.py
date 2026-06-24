# HGAC - Siemens RFID OPC reader
# Pegar dentro del handleTimerEvent() fijo de un Gateway Timer Script y
# aplicar un Tab a todo el bloque. Rate recomendado: 250 ms.

OPC_SERVER = "Ignition OPC UA Server"
PLC_DEVICE = "PLC_RFID"

TAG_PROVIDER = "[default]"
BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1/RFID"

# Items OPC del S7-1200 confirmados durante el POC.
# DB5 bytes 68, 69 y 70 contienen el mismo RSSI reportado por el RF680R.
# Se usa exclusivamente byte 68 como indicador de intensidad de senal.
DETECT_ITEM = "[%s]MX4.0" % PLC_DEVICE
EPC_ITEMS = ["[%s]DB5,B%d" % (PLC_DEVICE, index) for index in range(4, 16)]
RSSI_ITEM = "[%s]DB5,B68" % PLC_DEVICE


def now_text():
	return system.date.format(system.date.now(), "yyyy-MM-dd HH:mm:ss.SSS")


def write_map(values):
	paths = []
	data = []
	for name, value in values.items():
		paths.append(BASE + "/" + name)
		data.append(value)
	return system.tag.writeBlocking(paths, data)


def quality_good(qualified_value):
	try:
		return qualified_value.quality.isGood()
	except:
		return False


def format_epc(byte_values):
	return "".join(["%02X" % (int(value) & 0xFF) for value in byte_values])


logger = system.util.getLogger("HGAC.RFID.Siemens")

try:
	items = [DETECT_ITEM] + EPC_ITEMS + [RSSI_ITEM]
	values = system.opc.readValues(OPC_SERVER, items)

	if len(values) != len(items):
		raise Exception("Cantidad inesperada de valores OPC: %d/%d" % (len(values), len(items)))

	bad_items = []
	for index in range(len(values)):
		if not quality_good(values[index]):
			bad_items.append(items[index])

	if bad_items:
		write_map({
			"Trigger": False,
			"ReaderStatus": "BAD_OPC_QUALITY",
			"CommStatus": "ERROR",
			"LastUpdate": now_text(),
			"RawJson": system.util.jsonEncode({"bad_items": bad_items})
		})
	else:
		detected = bool(values[0].value)
		previous = system.tag.readBlocking([
			BASE + "/Trigger",
			BASE + "/TagEpc",
			BASE + "/ReadCount"
		])
		previous_trigger = bool(previous[0].value)
		previous_epc = str(previous[1].value or "")
		try:
			previous_count = int(previous[2].value or 0)
		except:
			previous_count = 0

		if not detected:
			write_map({
				"Trigger": False,
				"ReaderStatus": "WAITING_FOR_TAG",
				"CommStatus": "OK",
				"LastUpdate": now_text()
			})
		else:
			epc_bytes = [values[index].value for index in range(1, 13)]
			epc = format_epc(epc_bytes)
			rssi = float(values[13].value or 0.0)
			new_event = (not previous_trigger) or epc != previous_epc

			payload = {
				"source": "siemens_s7_opc",
				"timestamp": now_text(),
				"detected": True,
				"epc": epc,
				"epc_bytes": [int(value) & 0xFF for value in epc_bytes],
				"rssi": rssi,
				"opc_device": PLC_DEVICE
			}

			write_map({
				"Trigger": True,
				"TagEpc": epc,
				"TagTid": "",
				"ReaderIp": "",
				"ReaderName": "Siemens RFID / %s" % PLC_DEVICE,
				"Antenna": 1,
				"Rssi": rssi,
				"ReadCount": previous_count + (1 if new_event else 0),
				"ReadTimestamp": now_text() if new_event else system.tag.readBlocking([BASE + "/ReadTimestamp"])[0].value,
				"VehicleMatched": False,
				"PlateExpected": "",
				"ValidFormat": len(epc) == 24,
				"ReaderStatus": "TAG_DETECTED",
				"RawJson": system.util.jsonEncode(payload),
				"LastUpdate": now_text(),
				"CommStatus": "OK"
			})

except Exception as exc:
	message = "ERROR: %s" % str(exc)
	write_map({
		"Trigger": False,
		"ReaderStatus": message,
		"CommStatus": "ERROR",
		"LastUpdate": now_text()
	})
	logger.error(message)
