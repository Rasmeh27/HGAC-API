# HGAC - Trigger de grabacion RTSP desde Ignition
# Ubicacion recomendada:
#   Tag Change Script sobre Decision/Trigger o BioStar/Trigger.
#
# Objetivo:
# Ejecutar el capturador RTSP probado en Python para guardar evidencia MP4.
#
# Importante:
# system.util.execute ejecuta en el Gateway, no en el cliente Perspective.
# Las rutas deben existir en el servidor donde corre Ignition Gateway.

TAG_PROVIDER = "[default]"
BASE = TAG_PROVIDER + "HGAC/HainaOccidental/Entrada/Gate1/Lane1"

PYTHON_EXE = r"C:\Users\ByronRussell\AppData\Local\Programs\Python\Python312\python.exe"
CAPTURE_SCRIPT = r"C:\Users\ByronRussell\Documents\Claude\Projects\CRUCE VEHICULAR HIT\hgac_rtsp_camera_capture.py"

CAMERA_NAME = "P1 - Carril 1"
CAMERA_IP = "172.17.220.119"
DURATION_SECONDS = "10"
WIDTH = "1280"
HEIGHT = "720"


def p(rel):
    return BASE + "/" + rel


def now_text():
    return system.date.format(system.date.now(), "yyyy-MM-dd HH:mm:ss")


def write_map(values):
    paths = []
    vals = []
    for rel, val in values.items():
        paths.append(p(rel))
        vals.append(val)
    system.tag.writeBlocking(paths, vals)


try:
    # Si este script se usa como Tag Change Script, ignorar initialChange.
    try:
        if initialChange:
            pass
    except:
        pass

    args = [
        PYTHON_EXE,
        CAPTURE_SCRIPT,
        "--camera", CAMERA_NAME,
        "--ip", CAMERA_IP,
        "--duration", DURATION_SECONDS,
        "--reencode",
        "--scale", WIDTH + "x" + HEIGHT,
        "--event-id", system.date.format(system.date.now(), "yyyyMMdd-HHmmss"),
    ]

    write_map({
        "Camera/TriggerRecord": True,
        "Camera/Recording": True,
        "Camera/CameraName": CAMERA_NAME,
        "Camera/CameraIp": CAMERA_IP,
        "Camera/RecordStatus": "STARTED",
        "Camera/LastUpdate": now_text(),
    })

    system.util.execute(args)

    write_map({
        "Camera/Recording": False,
        "Camera/RecordStatus": "EXECUTED",
        "Camera/LastUpdate": now_text(),
    })

except Exception as e:
    write_map({
        "Camera/Recording": False,
        "Camera/RecordStatus": "ERROR: " + str(e),
        "Camera/LastUpdate": now_text(),
    })
    system.util.getLogger("HGAC").error("RTSP record trigger error: %s" % str(e))
