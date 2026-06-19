"""Captura desde webcam local usando OpenCV."""

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

# En Windows, DirectShow (CAP_DSHOW) suele ser el backend más estable para
# webcams USB; si falla se intenta Media Foundation (CAP_MSMF).
_WEBCAM_BACKENDS = (cv2.CAP_DSHOW, cv2.CAP_MSMF)


class WebcamCameraProvider(CameraProvider):
    def __init__(self, device_index: int = 0, jpeg_quality: int = 90) -> None:
        self._device_index = device_index
        self._jpeg_quality = jpeg_quality

    def capture_frame(self) -> bytes:
        logger.debug("Abriendo webcam index={}", self._device_index)
        capture = cv2.VideoCapture(self._device_index)
        if not capture.isOpened():
            raise CameraNotAvailableError(
                f"No se pudo abrir la webcam (index={self._device_index})"
            )

        try:
            success, frame = capture.read()
            if not success or frame is None:
                raise CameraTimeoutError("La webcam no devolvió frame")

            ok, buffer = cv2.imencode(
                ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if not ok:
                raise CameraTimeoutError("No se pudo codificar el frame a JPEG")
            return buffer.tobytes()
        finally:
            capture.release()

    def open_session(self, options: StreamOptions) -> CameraCaptureSession:
        capture = self._open_stream_capture(options)
        return OpenCvCaptureSession(capture, jpeg_quality=options.jpeg_quality)

    def _open_stream_capture(self, options: StreamOptions) -> cv2.VideoCapture:
        """Abre la webcam para streaming probando backends de Windows en orden."""
        for backend in _WEBCAM_BACKENDS:
            capture = cv2.VideoCapture(self._device_index, backend)
            if capture.isOpened():
                logger.info(
                    "Webcam index={} abierta para streaming (backend={})",
                    self._device_index,
                    backend,
                )
                self._apply_options(capture, options)
                return capture
            capture.release()

        raise CameraNotAvailableError(
            f"No se pudo abrir la webcam (index={self._device_index}) para streaming"
        )

    @staticmethod
    def _apply_options(capture: cv2.VideoCapture, options: StreamOptions) -> None:
        if options.width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, options.width)
        if options.height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, options.height)
        # Buffer mínimo: el preview prioriza el frame más reciente sobre el orden.
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # No se fija CAP_PROP_FPS: en DirectShow suele ralentizar la apertura y el
        # ritmo del stream ya se controla por software (frame_interval del worker).
