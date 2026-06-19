"""Jerarquía de excepciones del dominio.

Cada integración debe lanzar excepciones que hereden de `IntegrationError`
para que la capa API pueda mapearlas a respuestas HTTP coherentes sin
acoplarse a errores específicos de librerías (cv2, requests, selenium...).
"""


class AppError(Exception):
    """Error base de la aplicación."""


class IntegrationError(AppError):
    """Error genérico al hablar con un sistema externo."""


# --- Cámara ---
class CameraError(IntegrationError):
    """Error capturando frame desde la cámara."""


class CameraNotAvailableError(CameraError):
    """La cámara no pudo abrirse (índice/RTSP inválido o no conectado)."""


class CameraTimeoutError(CameraError):
    """Timeout al capturar frame."""


class CameraNotFoundError(AppError):
    """El `camera_id` solicitado no existe en el registro de cámaras.

    No hereda de `CameraError` (IntegrationError): es un fallo de búsqueda/
    configuración, no de comunicación con el hardware. La capa API la mapea
    a 404, no a 502/503.
    """


# --- LPR ---
class LprError(IntegrationError):
    """Error del servicio de reconocimiento de placas."""


class LprApiError(LprError):
    """Plate Recognizer devolvió un error HTTP o payload inválido."""


class LprPlateNotDetectedError(LprError):
    """La imagen no contiene placa detectable con suficiente confianza."""


# --- BioStar ---
class BioStarError(IntegrationError):
    """Error genérico con BioStar 2."""


class BioStarAuthenticationError(BioStarError):
    """Login fallido o sesión expirada."""


class BioStarUserNotFoundError(BioStarError):
    """No se encontró el usuario consultado."""


# --- RNTT ---
class RnttError(IntegrationError):
    """Error consultando RNTT."""


class RnttTimeoutError(RnttError):
    """El portal no respondió a tiempo."""


class RnttPlateNotFoundError(RnttError):
    """La placa no existe en el portal."""


# --- Ignition ---
class IgnitionError(IntegrationError):
    """Error escribiendo o enviando a Ignition."""
