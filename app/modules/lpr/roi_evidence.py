"""Recorte de ROI y evidencia visual (overlay) para depurar el pipeline LPR.

Funciones puras sobre `np.ndarray` BGR (OpenCV). No tocan disco ni cámara: el
servicio decide qué guardar. El overlay dibuja sobre una COPIA del frame; nunca
muta la imagen original (que es la evidencia del frame completo).

Convención de colores (BGR):
- placa  -> verde
- rótulo -> azul
- bbox detectado por el motor (SimpleLPR) -> rojo
"""

from __future__ import annotations

import cv2
import numpy as np

# BGR
_COLOR_PLATE = (0, 200, 0)
_COLOR_ROTULO = (255, 120, 0)
_COLOR_BBOX = (0, 0, 255)
_FONT = cv2.FONT_HERSHEY_SIMPLEX


def clamp_roi(
    image: np.ndarray, x: int, y: int, width: int, height: int
) -> tuple[np.ndarray, dict | None]:
    """Recorta `image` a (x, y, width, height) acotado a los bordes del frame.

    Devuelve ``(recorte, roi_aplicado)``:
    - Si width/height <= 0 (ROI deshabilitado): el frame completo y ``None``.
    - Si el ROI queda fuera del frame (intersección vacía): el frame completo y
      ``None``, para no entregar una imagen vacía al OCR.
    - En otro caso: el recorte (vista NumPy, sin copia) y el dict del ROI
      REALMENTE aplicado (acotado).
    """
    if width <= 0 or height <= 0:
        return image, None

    h, w = image.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(w, x + width)
    y2 = min(h, y + height)
    if x2 <= x1 or y2 <= y1:
        return image, None

    cropped = image[y1:y2, x1:x2]
    return cropped, {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}


def encode_jpeg(image: np.ndarray, quality: int = 90) -> bytes | None:
    """Codifica un BGR a JPEG. Devuelve None si OpenCV falla."""
    if image is None or image.size == 0:
        return None
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return None
    return buffer.tobytes()


def _draw_box(frame: np.ndarray, roi: dict | None, color, label: str) -> None:
    if not roi:
        return
    x, y = int(roi["x"]), int(roi["y"])
    x2, y2 = x + int(roi["width"]), y + int(roi["height"])
    cv2.rectangle(frame, (x, y), (x2, y2), color, 2)
    # Etiqueta encima del recuadro (o dentro si está pegado al borde superior).
    text_y = y - 8 if y - 8 > 10 else y + 18
    cv2.putText(frame, label, (x, text_y), _FONT, 0.6, color, 2, cv2.LINE_AA)


def render_overlay(
    frame_bgr: np.ndarray,
    *,
    plate_roi: dict | None = None,
    rotulo_roi: dict | None = None,
    plate_bbox: dict | None = None,
    plate_label: str | None = None,
    rotulo_label: str | None = None,
    quality: int = 85,
) -> bytes | None:
    """Dibuja los ROI (placa/rótulo) y el bbox del motor sobre una copia del frame.

    Devuelve el JPEG del overlay, o None si no hay nada que dibujar / falla la
    codificación. No muta `frame_bgr`.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return None
    if not (plate_roi or rotulo_roi or plate_bbox):
        return None

    canvas = frame_bgr.copy()
    plate_text = "plate_roi" + (f": {plate_label}" if plate_label else "")
    _draw_box(canvas, plate_roi, _COLOR_PLATE, plate_text)
    rotulo_text = "rotulo_roi" + (f": {rotulo_label}" if rotulo_label else "")
    _draw_box(canvas, rotulo_roi, _COLOR_ROTULO, rotulo_text)
    _draw_box(canvas, plate_bbox, _COLOR_BBOX, "simplelpr_bbox")
    return encode_jpeg(canvas, quality)
