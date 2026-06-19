"""Pantalla de monitoreo en vivo para la cámara y pruebas LPR.

Este módulo expone una vista web mínima para la PoC:
- Stream MJPEG de la cámara configurada.
- Botón para disparar /lpr/read.
- Modo automático de lectura cada pocos segundos.

No ejecuta OCR por cada frame del video porque eso sería costoso e inestable
con EasyOCR en CPU. La lectura LPR se dispara bajo demanda o por intervalo.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from urllib.parse import urlparse

import cv2
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse
from loguru import logger

from app.api.dependencies import settings_provider
from app.core.config import Settings

router = APIRouter(prefix="/monitor", tags=["Monitor"])


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def monitor_page() -> HTMLResponse:
    """Devuelve la pantalla web de monitoreo."""
    return HTMLResponse(content=_MONITOR_HTML)


@router.get("/status")
def monitor_status(settings: Settings = Depends(settings_provider)) -> dict[str, str | int]:
    """Devuelve información segura de la cámara configurada.

    No expone usuario ni contraseña de la URL RTSP.
    """
    return {
        "camera_provider": settings.camera_provider,
        "camera_source": _build_safe_camera_source(settings),
        "monitor_fps": settings.monitor_fps,
    }

@router.get("/video")
def video_stream(settings: Settings = Depends(settings_provider)) -> StreamingResponse:
    """Stream MJPEG para visualizar la cámara en el navegador."""
    source = _build_camera_source(settings)

    if source is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Fuente de cámara no configurada",
        )

    return StreamingResponse(
        _generate_mjpeg_stream(
            source=source,
            fps=settings.monitor_fps,
            jpeg_quality=settings.monitor_jpeg_quality,
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


def _build_camera_source(settings: Settings) -> str | int | None:
    if settings.camera_provider == "rtsp":
        return settings.rtsp_url or None
    return settings.webcam_index


def _build_safe_camera_source(settings: Settings) -> str:
    if settings.camera_provider != "rtsp":
        return f"webcam:{settings.webcam_index}"

    parsed_url = urlparse(settings.rtsp_url)
    if not parsed_url.hostname:
        return "rtsp:no-configurado"

    port = f":{parsed_url.port}" if parsed_url.port else ""
    path = parsed_url.path or ""
    return f"{parsed_url.scheme}://{parsed_url.hostname}{port}{path}"


def _generate_mjpeg_stream(
    source: str | int,
    fps: int,
    jpeg_quality: int,
) -> Generator[bytes, None, None]:
    capture = cv2.VideoCapture(source)

    if not capture.isOpened():
        logger.error("Monitor: no se pudo abrir la cámara source={}", source)
        return

    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    safe_fps = max(1, min(fps, 30))
    safe_jpeg_quality = max(40, min(jpeg_quality, 95))
    frame_delay_seconds = 1 / safe_fps

    try:
        while True:
            success, frame = capture.read()

            if not success or frame is None:
                logger.warning("Monitor: la cámara no devolvió frame")
                time.sleep(1)
                continue

            ok, buffer = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), safe_jpeg_quality],
            )

            if not ok:
                logger.warning("Monitor: no se pudo codificar frame a JPEG")
                time.sleep(frame_delay_seconds)
                continue

            frame_bytes = buffer.tobytes()

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n\r\n"
                + frame_bytes
                + b"\r\n"
            )

            time.sleep(frame_delay_seconds)
    finally:
        capture.release()
        logger.info("Monitor: stream de cámara cerrado")


_MONITOR_HTML = """
<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8" />
    <title>HGAC PoC - Monitor de Cámara</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
        :root {
            color-scheme: dark;
            font-family: Arial, Helvetica, sans-serif;
            background: #111827;
            color: #e5e7eb;
        }

        body {
            margin: 0;
            background: #111827;
        }

        header {
            padding: 16px 24px;
            border-bottom: 1px solid #374151;
            background: #0f172a;
        }

        header h1 {
            margin: 0;
            font-size: 20px;
            font-weight: 700;
        }

        header p {
            margin: 6px 0 0;
            color: #9ca3af;
            font-size: 14px;
        }

        main {
            display: grid;
            grid-template-columns: minmax(0, 1fr) 360px;
            gap: 16px;
            padding: 16px;
        }

        .video-card,
        .panel {
            background: #1f2937;
            border: 1px solid #374151;
            border-radius: 12px;
            overflow: hidden;
        }

        .video-toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 14px;
            border-bottom: 1px solid #374151;
            background: #111827;
        }

        .video-toolbar strong {
            font-size: 14px;
        }

        .video-wrapper {
            background: #000;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 520px;
        }

        .video-wrapper img {
            width: 100%;
            height: auto;
            display: block;
        }

        .panel {
            padding: 16px;
        }

        .panel h2 {
            margin: 0 0 12px;
            font-size: 18px;
        }

        .field {
            margin-bottom: 14px;
        }

        .label {
            display: block;
            font-size: 12px;
            color: #9ca3af;
            margin-bottom: 4px;
        }

        .value {
            font-size: 14px;
            word-break: break-word;
        }

        .plate {
            font-size: 32px;
            font-weight: 800;
            letter-spacing: 2px;
            color: #f9fafb;
        }

        .status {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            background: #374151;
            color: #e5e7eb;
        }

        .status.ok {
            background: #065f46;
            color: #d1fae5;
        }

        .status.error {
            background: #7f1d1d;
            color: #fee2e2;
        }

        .status.loading {
            background: #78350f;
            color: #fef3c7;
        }

        button {
            width: 100%;
            border: 0;
            border-radius: 8px;
            padding: 12px 14px;
            margin-bottom: 10px;
            background: #2563eb;
            color: white;
            font-weight: 700;
            cursor: pointer;
        }

        button:hover {
            background: #1d4ed8;
        }

        button.secondary {
            background: #374151;
        }

        button.secondary:hover {
            background: #4b5563;
        }

        pre {
            white-space: pre-wrap;
            word-break: break-word;
            background: #111827;
            border: 1px solid #374151;
            border-radius: 8px;
            padding: 10px;
            font-size: 12px;
            color: #d1d5db;
            max-height: 220px;
            overflow: auto;
        }

        @media (max-width: 980px) {
            main {
                grid-template-columns: 1fr;
            }

            .video-wrapper {
                min-height: 300px;
            }
        }
    </style>
</head>
<body>
    <header>
        <h1>HGAC PoC - Monitor de Cámara</h1>
        <p>Vista en vivo de la cámara configurada y pruebas manuales de lectura LPR.</p>
    </header>

    <main>
        <section class="video-card">
            <div class="video-toolbar">
                <strong>Video en vivo</strong>
                <span id="cameraStatus" class="status">Inicializando</span>
            </div>
            <div class="video-wrapper">
                <img id="cameraFeed" src="/monitor/video" alt="Video en vivo de la cámara" />
            </div>
        </section>

        <aside class="panel">
            <h2>Lectura de placa</h2>

            <div class="field">
                <span class="label">Estado LPR</span>
                <span id="lprStatus" class="status">Sin lectura</span>
            </div>

            <div class="field">
                <span class="label">Última placa</span>
                <div id="plateValue" class="plate">---</div>
            </div>

            <div class="field">
                <span class="label">Confianza</span>
                <div id="confidenceValue" class="value">---</div>
            </div>

            <div class="field">
                <span class="label">Fecha/hora</span>
                <div id="timestampValue" class="value">---</div>
            </div>

            <button type="button" onclick="readPlateNow()">Leer placa ahora</button>
            <button id="autoButton" class="secondary" type="button" onclick="toggleAutoRead()">
                Activar lectura automática
            </button>

            <div class="field">
                <span class="label">Cámara</span>
                <div id="cameraInfo" class="value">Cargando...</div>
            </div>

            <div class="field">
                <span class="label">Respuesta técnica</span>
                <pre id="rawResponse">{}</pre>
            </div>
        </aside>
    </main>

    <script>
        let autoReadEnabled = false;
        let autoReadTimer = null;
        const AUTO_READ_INTERVAL_MS = 3000;

        async function loadMonitorStatus() {
            try {
                const response = await fetch('/monitor/status');
                const data = await response.json();

                document.getElementById('cameraInfo').textContent =
                    `${data.camera_provider} | ${data.camera_source} | ${data.monitor_fps} FPS`;

                setCameraStatus('Conectada', 'ok');
            } catch (error) {
                document.getElementById('cameraInfo').textContent = 'No disponible';
                setCameraStatus('Error', 'error');
            }
        }

        async function readPlateNow() {
            setLprStatus('Leyendo...', 'loading');

            try {
                const response = await fetch('/lpr/read', {
                    method: 'POST',
                    headers: {
                        'Accept': 'application/json'
                    }
                });

                const data = await response.json();
                document.getElementById('rawResponse').textContent =
                    JSON.stringify(data, null, 2);

                if (!response.ok) {
                    const detail = data.detail || data.error || 'No se pudo leer placa';
                    setLprStatus(detail, 'error');
                    document.getElementById('plateValue').textContent = '---';
                    document.getElementById('confidenceValue').textContent = '---';
                    document.getElementById('timestampValue').textContent = new Date().toLocaleString();
                    return;
                }

                setLprStatus('Placa detectada', 'ok');
                document.getElementById('plateValue').textContent = data.plate || '---';
                document.getElementById('confidenceValue').textContent =
                    typeof data.confidence === 'number'
                        ? `${(data.confidence * 100).toFixed(1)}%`
                        : '---';
                document.getElementById('timestampValue').textContent =
                    data.timestamp || new Date().toISOString();
            } catch (error) {
                setLprStatus('Error llamando /lpr/read', 'error');
                document.getElementById('rawResponse').textContent = String(error);
            }
        }

        function toggleAutoRead() {
            autoReadEnabled = !autoReadEnabled;
            const button = document.getElementById('autoButton');

            if (autoReadEnabled) {
                button.textContent = 'Desactivar lectura automática';
                readPlateNow();
                autoReadTimer = setInterval(readPlateNow, AUTO_READ_INTERVAL_MS);
                return;
            }

            button.textContent = 'Activar lectura automática';

            if (autoReadTimer !== null) {
                clearInterval(autoReadTimer);
                autoReadTimer = null;
            }
        }

        function setCameraStatus(text, mode) {
            const element = document.getElementById('cameraStatus');
            element.textContent = text;
            element.className = `status ${mode}`;
        }

        function setLprStatus(text, mode) {
            const element = document.getElementById('lprStatus');
            element.textContent = text;
            element.className = `status ${mode}`;
        }

        document.getElementById('cameraFeed').addEventListener('load', () => {
            setCameraStatus('Conectada', 'ok');
        });

        document.getElementById('cameraFeed').addEventListener('error', () => {
            setCameraStatus('Sin video', 'error');
        });

        loadMonitorStatus();
    </script>
</body>
</html>
"""