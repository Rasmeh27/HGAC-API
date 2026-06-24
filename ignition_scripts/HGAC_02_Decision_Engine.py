# HGAC - Decision Engine
# Opcion A: pegar en Gateway Event Script > Timer, Rate 500-1000 ms.
# Opcion B: pegar en Tag Change Script disparado por BioStar/Trigger, RFID/Trigger o LPR/Trigger.
#
# Objetivo:
# Consolidar BioStar, RFID, LPR, RNTT, Navis y hardware para producir
# Decision/FinalDecision y comandos a barrera/semaforo.

TAG_PROVIDER = "[default]"
BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1"

MIN_LPR_CONFIDENCE = 80.0
REQUIRE_RFID = True
REQUIRE_LPR = False
REQUIRE_BIOSTAR = True
REQUIRE_RNTT = True
REQUIRE_NAVIS = False
ENABLE_OUTPUT_COMMANDS = False


def p(rel):
    return BASE + "/" + rel


def read_map(relative_paths):
    values = system.tag.readBlocking([p(x) for x in relative_paths])
    data = {}
    for i in range(len(relative_paths)):
        data[relative_paths[i]] = values[i].value
    return data


def write_map(values):
    paths = []
    vals = []
    for rel, val in values.items():
        paths.append(p(rel))
        vals.append(val)
    system.tag.writeBlocking(paths, vals)


def now_text():
    return system.date.format(system.date.now(), "yyyy-MM-dd HH:mm:ss")


def bool_value(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in ["TRUE", "1", "SI", "YES", "OK", "PASA", "ACTIVO"]


def text(value):
    if value is None:
        return ""
    return str(value)


def evaluate():
    tags = read_map([
        "BioStar/Pasa",
        "BioStar/Decision",
        "BioStar/UsuarioId",
        "BioStar/UsuarioNombre",
        "BioStar/Metodo",
        "RFID/TagEpc",
        "RFID/VehicleMatched",
        "RFID/PlateExpected",
        "LPR/PlateNormalized",
        "LPR/Confidence",
        "LPR/PlateMatched",
        "RNTT/Authorized",
        "RNTT/DriverInterpretedStatus",
        "RNTT/DriverInterpretedReason",
        "RNTT/TruckEstadoText",
        "Navis/Status",
        "Navis/LifeCycleState",
        "Wialon/InsideTerminal",
        "GateHardware/LoopOccupied",
    ])

    reasons = []
    warnings = []

    biostar_ok = bool_value(tags["BioStar/Pasa"])
    if REQUIRE_BIOSTAR and not biostar_ok:
        reasons.append("BioStar no aprobado")

    rfid_ok = bool_value(tags["RFID/VehicleMatched"]) or len(text(tags["RFID/TagEpc"])) > 0
    if REQUIRE_RFID and not rfid_ok:
        reasons.append("RFID no detectado o no asociado")

    lpr_plate = text(tags["LPR/PlateNormalized"])
    lpr_conf = float(tags["LPR/Confidence"] or 0.0)
    lpr_ok = bool_value(tags["LPR/PlateMatched"]) or (len(lpr_plate) > 0 and lpr_conf >= MIN_LPR_CONFIDENCE)
    if REQUIRE_LPR and not lpr_ok:
        reasons.append("LPR no aprobado o confianza baja")
    elif len(lpr_plate) > 0 and lpr_conf < MIN_LPR_CONFIDENCE:
        warnings.append("LPR confianza baja: %.1f" % lpr_conf)

    rntt_ok = bool_value(tags["RNTT/Authorized"])
    if REQUIRE_RNTT and not rntt_ok:
        driver_status = text(tags["RNTT/DriverInterpretedStatus"])
        driver_reason = text(tags["RNTT/DriverInterpretedReason"])
        truck_status = text(tags["RNTT/TruckEstadoText"])
        reasons.append("RNTT no aprobado: chofer=%s camion=%s %s" % (driver_status, truck_status, driver_reason))

    navis_status = text(tags["Navis/Status"]).upper()
    navis_life = text(tags["Navis/LifeCycleState"]).upper()
    navis_ok = navis_status in ["OK", ""] and navis_life in ["ACTIVE", ""]
    if REQUIRE_NAVIS and not navis_ok:
        reasons.append("Navis no aprobado: %s %s" % (navis_status, navis_life))

    if len(reasons) == 0:
        decision = "PASA"
        code = "ALLOW"
        reason = "Validacion aprobada"
        open_barrier = True
        red = False
        green = True
        alarm = False
    else:
        decision = "NO_PASA"
        code = "DENY"
        reason = "; ".join(reasons)
        open_barrier = False
        red = True
        green = False
        alarm = True

    if len(warnings) > 0 and decision == "PASA":
        reason = reason + " | Advertencia: " + "; ".join(warnings)

    tx_id = "HGAC-" + system.date.format(system.date.now(), "yyyyMMdd-HHmmss")

    write_map({
        "Decision/TransactionId": tx_id,
        "Decision/Trigger": True,
        "Decision/FinalDecision": decision,
        "Decision/DecisionCode": code,
        "Decision/DecisionReason": reason,
        "Decision/DecisionTimestamp": now_text(),
        "Decision/RfidOk": rfid_ok,
        "Decision/LprOk": lpr_ok,
        "Decision/BioStarOk": biostar_ok,
        "Decision/RnttOk": rntt_ok,
        "Decision/NavisOk": navis_ok,
        "Decision/Plate": lpr_plate,
        "Decision/TagEpc": text(tags["RFID/TagEpc"]),
        "Decision/UserId": text(tags["BioStar/UsuarioId"]),
        "Decision/UserName": text(tags["BioStar/UsuarioNombre"]),
        "Decision/OpenBarrier": open_barrier,
        "Decision/TurnGreenLight": green,
        "Decision/TurnRedLight": red,
        "Decision/AlarmActive": alarm,
        "Decision/AlarmMessage": "" if not alarm else reason,
        "Decision/LastUpdate": now_text(),
        "Transaction/TransactionId": tx_id,
        "Transaction/State": "DECIDED",
        "Transaction/PrimaryTrigger": "BioStar/LPR/RFID",
        "Transaction/LastError": "",
    })

    if ENABLE_OUTPUT_COMMANDS:
        write_map({
            "GateHardware/BarrierOpenCmd": open_barrier,
            "GateHardware/TrafficLightGreen": green,
            "GateHardware/TrafficLightRed": red,
        })


try:
    evaluate()
except Exception as e:
    system.util.getLogger("HGAC").error("HGAC decision error: %s" % str(e))
    write_map({
        "Decision/FinalDecision": "ERROR",
        "Decision/DecisionCode": "ERROR",
        "Decision/DecisionReason": str(e),
        "Decision/AlarmActive": True,
        "Decision/AlarmMessage": str(e),
        "Decision/LastUpdate": now_text(),
    })
