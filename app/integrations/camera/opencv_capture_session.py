"""Sesión de captura persistente basada en OpenCV.

Envuelve un `cv2.VideoCapture` ya abierto y codifica cada frame a JPEG. La usan
los proveedores webcam y RTSP para el stream en vivo, así la lógica de
lectura/codificación vive en un solo lugar.
"""

from __future__ import annotations

import cv2

from app.integrations.camera.camera_provider import CameraCaptureSession


class OpenCvCaptureSession(CameraCaptureSession):
    def __init__(self, capture: cv2.VideoCapture, jpeg_quality: int = 75) -> None:
        self._capture = capture
        self._jpeg_quality = jpeg_quality

    def read_jpeg(self) -> bytes | None:
        success, frame = self._capture.read()
        if not success or frame is None:
            return None

        ok, buffer = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if not ok:
            return None
        return buffer.tobytes()

    def release(self) -> None:
        self._capture.release()
