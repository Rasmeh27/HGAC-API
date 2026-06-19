"""Captura desde un stream RTSP usando OpenCV.

Preparado para uso futuro con cámaras IP (p.ej. ONVIF/RTSP). En la PoC
inicial se usa `WebcamCameraProvider`, pero esta clase ya está lista
para activarse vía configuración (`CAMERA_PROVIDER=rtsp`), tanto para
captura puntual como para el stream MJPEG en vivo.
"""

from __future__ import annotations

import cv2
from loguru import logger

from app.core.errors import CameraNotAvailableError, CameraTimeoutError
from app.integrations.camera.camera_provider import (
    CameraCaptureSession,
    CameraProvider,
    StreamOptions,
)
from app.integrations.camera.opencv_capture_session import OpenCvCaptureSession


class RtspCameraProvider(CameraProvider):
    def __init__(self, rtsp_url: str, jpeg_quality: int = 90) -> None:
        if not rtsp_url:
            raise CameraNotAvailableError("RTSP_URL no configurado")
        self._rtsp_url = rtsp_url
        self._jpeg_quality = jpeg_quality

    def capture_frame(self) -> bytes:
        logger.debug("Abriendo RTSP stream")
        capture = cv2.VideoCapture(self._rtsp_url)
        if not capture.isOpened():
            raise CameraNotAvailableError("No se pudo conectar al stream RTSP")

        try:
            success, frame = capture.read()
            if not success or frame is None:
                raise CameraTimeoutError("RTSP no devolvió frame")

            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if not ok:
                raise CameraTimeoutError("No se pudo codificar el frame a JPEG")
            return buffer.tobytes()
        finally:
            capture.release()

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        capture = cv2.VideoCapture(self._rtsp_url)
        if not capture.isOpened():
            capture.release()
            raise CameraNotAvailableError(
                "No se pudo conectar al stream RTSP para streaming"
            )
        logger.info("RTSP stream abierto para streaming")
        return OpenCvCaptureSession(capture, jpeg_quality=options.jpeg_quality)
