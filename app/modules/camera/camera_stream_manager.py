"""Gestor de streams MJPEG en vivo.

Mantiene **una sola apertura** de cada cámara mientras haya clientes conectados,
en un worker dedicado por `camera_id`. Cada worker:

- abre la fuente una vez (vía `CameraProvider.open_session`),
- captura frames en un loop a ~FPS configurados,
- guarda el último frame JPEG en memoria,
- lo sirve a N generadores de cliente (el preview descarta frames atrasados),
- libera la cámara cuando el último cliente se desconecta.

Es thread-safe: un `Lock` de gestor protege el mapa de workers y el conteo de
clientes; cada worker usa una `Condition` para el hand-off de frames. No abre
`cv2.VideoCapture` por frame ni permite dos aperturas simultáneas de la misma
cámara.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Callable

from loguru import logger

from app.core.errors import CameraError, CameraNotAvailableError, CameraTimeoutError
from app.integrations.camera.camera_provider import (
    CameraCaptureSession,
    CameraProvider,
    StreamOptions,
)
from app.modules.camera.camera_registry import CameraConfig

ProviderFactory = Callable[[CameraConfig], CameraProvider]

# Cuánto espera un generador de cliente por un frame nuevo antes de revisar el
# estado de parada (mantiene el loop receptivo a desconexiones).
_FRAME_WAIT_TIMEOUT_SECONDS = 5.0
# Tras este número de lecturas fallidas seguidas, el worker se da por caído.
_MAX_CONSECUTIVE_READ_FAILURES = 30


class _CameraWorker:
    """Mantiene una cámara abierta y publica el último frame JPEG en memoria."""

    def __init__(
        self,
        config: CameraConfig,
        provider_factory: ProviderFactory,
        options: StreamOptions,
    ) -> None:
        self._config = config
        self._provider_factory = provider_factory
        self._options = options
        self._frame_interval = 1.0 / options.fps if options.fps else 0.1

        self._cond = threading.Condition()
        self._latest_jpeg: bytes | None = None
        self._seq = 0
        self._error: CameraError | None = None
        self._stop = threading.Event()
        self._clients = 0
        self._thread = threading.Thread(
            target=self._run,
            name=f"camera-stream-{config.camera_id}",
            daemon=True,
        )

    # --- ciclo de vida (invocado bajo el lock del gestor) ---

    def start(self) -> None:
        self._thread.start()

    def add_client(self) -> None:
        self._clients += 1

    def remove_client(self) -> int:
        self._clients -= 1
        return self._clients

    @property
    def client_count(self) -> int:
        return self._clients

    def stop(self) -> None:
        """Señaliza la parada y espera a que el worker libere la cámara."""
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        self._thread.join(timeout=_FRAME_WAIT_TIMEOUT_SECONDS)

    # --- acceso a frames ---

    def latest_jpeg(self) -> bytes | None:
        with self._cond:
            return self._latest_jpeg

    def wait_first_frame(self, timeout: float) -> None:
        """Bloquea hasta el primer frame, o lanza `CameraError` si no abre.

        Permite que el endpoint devuelva 503 *antes* de empezar a emitir el
        stream, en vez de cortar a mitad de respuesta.
        """
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._latest_jpeg is None and self._error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)

            if self._error is not None:
                raise self._error
            if self._latest_jpeg is None:
                raise CameraTimeoutError(
                    f"La cámara '{self._config.camera_id}' no entregó frame a tiempo"
                )

    def frames(self) -> Iterator[bytes]:
        """Emite el último frame disponible cada vez que cambia (descarta atraso)."""
        last_seq = 0
        while not self._stop.is_set():
            with self._cond:
                updated = self._cond.wait_for(
                    lambda: self._seq != last_seq or self._stop.is_set(),
                    timeout=_FRAME_WAIT_TIMEOUT_SECONDS,
                )
                if self._stop.is_set():
                    break
                if not updated:
                    continue
                frame = self._latest_jpeg
                last_seq = self._seq
            if frame is not None:
                yield frame

    # --- loop de captura ---

    def _run(self) -> None:
        try:
            session = self._open_session()
        except CameraError as exc:
            logger.warning(
                "Stream {}: no se pudo abrir la cámara: {}", self._config.camera_id, exc
            )
            self._publish_error(exc)
            return
        except Exception as exc:  # noqa: BLE001 - cualquier fallo se normaliza a CameraError
            logger.exception(
                "Stream {}: error inesperado abriendo la cámara",
                self._config.camera_id,
            )
            self._publish_error(CameraNotAvailableError(str(exc)))
            return

        logger.info(
            "Stream {}: cámara abierta, capturando a ~{} FPS",
            self._config.camera_id,
            self._options.fps,
        )
        consecutive_failures = 0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    jpeg = session.read_jpeg()
                except Exception:  # noqa: BLE001 - una lectura no debe tumbar el worker
                    logger.exception("Stream {}: error leyendo frame", self._config.camera_id)
                    jpeg = None

                if jpeg is None:
                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_READ_FAILURES:
                        self._publish_error(
                            CameraTimeoutError(
                                f"La cámara '{self._config.camera_id}' dejó de "
                                "entregar frames"
                            )
                        )
                        break
                    self._stop.wait(self._frame_interval)
                    continue

                consecutive_failures = 0
                self._publish_frame(jpeg)

                # Sleep interrumpible: respeta el FPS objetivo y reacciona a stop().
                elapsed = time.monotonic() - started
                remaining = self._frame_interval - elapsed
                if remaining > 0:
                    self._stop.wait(remaining)
        finally:
            session.release()
            logger.info("Stream {}: cámara liberada", self._config.camera_id)

    def _open_session(self) -> CameraCaptureSession:
        provider = self._provider_factory(self._config)
        return provider.open_session(self._options)

    def _publish_frame(self, jpeg: bytes) -> None:
        with self._cond:
            self._latest_jpeg = jpeg
            self._seq += 1
            self._cond.notify_all()

    def _publish_error(self, exc: CameraError) -> None:
        with self._cond:
            self._error = exc
            self._stop.set()
            self._cond.notify_all()


class CameraStreamManager:
    """Coordina los workers de stream por cámara (singleton del proceso)."""

    def __init__(
        self,
        provider_factory: ProviderFactory,
        options: StreamOptions,
        first_frame_timeout: float = 5.0,
    ) -> None:
        self._provider_factory = provider_factory
        self._options = options
        self._first_frame_timeout = first_frame_timeout
        self._workers: dict[str, _CameraWorker] = {}
        self._lock = threading.Lock()

    def latest_frame(self, camera_id: str) -> bytes | None:
        """Último frame de un stream activo, o `None` si la cámara no se está
        transmitiendo. Permite a snapshot/evidencia reusar el frame en vivo y no
        abrir el dispositivo en paralelo."""
        with self._lock:
            worker = self._workers.get(camera_id)
        return worker.latest_jpeg() if worker is not None else None

    def open_stream(self, config: CameraConfig) -> Iterator[bytes]:
        """Registra un cliente y devuelve un iterador de frames JPEG.

        Función normal (no generador): abre/espera el primer frame de forma
        síncrona para poder propagar `CameraError` -> 503 antes de transmitir.
        El iterador devuelto libera el cliente al cerrarse (desconexión).
        """
        worker = self._acquire(config)
        try:
            worker.wait_first_frame(self._first_frame_timeout)
        except BaseException:
            self._release(config.camera_id)
            raise
        return self._client_frames(config.camera_id, worker)

    def shutdown(self) -> None:
        """Detiene todos los workers y libera todas las cámaras."""
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:
            worker.stop()

    def _client_frames(
        self, camera_id: str, worker: _CameraWorker
    ) -> Iterator[bytes]:
        try:
            yield from worker.frames()
        finally:
            self._release(camera_id)

    def _acquire(self, config: CameraConfig) -> _CameraWorker:
        with self._lock:
            worker = self._workers.get(config.camera_id)
            if worker is None:
                worker = _CameraWorker(config, self._provider_factory, self._options)
                self._workers[config.camera_id] = worker
                worker.start()
            worker.add_client()
            return worker

    def _release(self, camera_id: str) -> None:
        with self._lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return
            if worker.remove_client() > 0:
                return
            del self._workers[camera_id]
        # Parar fuera del lock: join no debe bloquear a otras cámaras.
        worker.stop()
