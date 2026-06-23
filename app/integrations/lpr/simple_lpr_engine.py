"""Motor LPR alternativo basado en SimpleLPR (adaptado a placas RD).

Implementa el mismo contrato `LprEngine` que el motor OpenCV+EasyOCR: recibe un
frame BGR (capturado por `CameraService`) y devuelve un `LprEngineResult`. NO
abre la cámara ni RTSP por su cuenta; NO decide el estado final ni valida formato
(eso es del servicio + catálogo dominicano).

Particularidades respecto al motor OpenCV:

* SimpleLPR no tiene plantilla de República Dominicana. Se activan países de
  alfabeto latino vecinos (Colombia/Puerto Rico/Venezuela por defecto) SOLO para
  habilitar OCR alfanumérico; NO son autoridad de formato.
* Como el OCR con plantillas vecinas confunde letra<->dígito, se generan
  candidatos por corrección posicional (`plate_ocr_correction`), trazables y con
  su nº de sustituciones. La corrección se penaliza en confianza para no aceptar
  como definitiva una placa que solo cuadra tras demasiados cambios. El OCR crudo
  se conserva en `candidate_scores` (clave `ocr_text`).
* SimpleLPR es dependencia OPCIONAL: se importa de forma perezosa. Si el motor
  activo no es SimpleLPR, el backend no requiere el paquete.

Integración de imagen: el binding de SimpleLPR analiza imágenes desde archivo
(`processor.analyze(path)`, documentado). Para no asumir una API en memoria no
verificada, se escribe el frame a un archivo temporal controlado y se elimina
tras analizar. Las sobrecargas en memoria (`analyze` con bytes / buffer crudo)
existen en el SDK C++ y quedan como optimización futura una vez verificadas
contra el wheel instalado.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

import cv2
import numpy as np
from loguru import logger

from app.core.errors import LprError
from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.integrations.lpr.plate_ocr_correction import generate_candidates
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.plate_validator import PlateValidator

ENGINE_NAME = "simplelpr_rd_poc"

# El formato válido domina la selección (igual filosofía que el motor OpenCV).
_FORMAT_BONUS = 1000.0
_MAX_DEBUG_ITEMS = 8


def _import_simplelpr():
    """Importa SimpleLPR de forma perezosa con un error claro si falta.

    Aislado en una función para poder sustituirlo en tests sin el paquete real.
    """
    try:
        import simplelpr  # type: ignore
    except ImportError as exc:  # pragma: no cover - depende del entorno
        raise LprError(
            "SimpleLPR no está instalado. Instala la dependencia "
            "(pip install SimpleLPR) o cambia LPR_ENGINE."
        ) from exc
    return simplelpr


@dataclass(frozen=True)
class SimpleLprConfig:
    countries: tuple[str, ...] = ("19", "74", "96")
    product_key_path: str = ""
    min_confidence: float = 55.0
    use_gpu: bool = False
    cuda_device_id: int = -1
    max_concurrent_ops: int = 0
    plate_region_detection: bool = True
    crop_to_plate_region: bool = False
    max_substitutions: int = 2
    substitution_penalty: float = 12.0
    jpeg_quality: int = 90


class SimpleLprEngine(LprEngine):
    def __init__(
        self,
        config: SimpleLprConfig,
        catalog: DominicanPlatePatternCatalog | None = None,
        validator: PlateValidator | None = None,
        simplelpr_module=None,
    ) -> None:
        # Import perezoso: si SimpleLPR no está, esto lanza LprError (claro) y la
        # factory lo traduce a 503. El backend NO se rompe si el motor activo es
        # otro, porque este __init__ solo se invoca al construir este motor.
        self._sl = simplelpr_module or _import_simplelpr()
        self._config = config
        self._catalog = catalog
        self._validator = validator or PlateValidator()
        self._engine = None
        self._processor = None
        self._configure()

    @property
    def name(self) -> str:
        return ENGINE_NAME

    # ---- configuración del motor SimpleLPR ----

    def _configure(self) -> None:
        sl = self._sl
        parms = sl.EngineSetupParms()
        parms.cudaDeviceId = self._config.cuda_device_id
        parms.enableImageProcessingWithGPU = self._config.use_gpu
        parms.enableClassificationWithGPU = self._config.use_gpu
        parms.maxConcurrentImageProcessingOps = self._config.max_concurrent_ops

        engine = sl.SimpleLPR(parms)
        self._log_version(engine)

        if self._config.product_key_path:
            try:
                engine.set_productKey(self._config.product_key_path)
            except Exception as exc:  # noqa: BLE001 - error nativo opaco del binding
                raise LprError(
                    f"No se pudo cargar la licencia SimpleLPR "
                    f"'{self._config.product_key_path}': {exc}"
                ) from exc

        self._configure_countries(engine)

        processor = engine.createProcessor()
        self._set_if_present(
            processor, "plateRegionDetectionEnabled", self._config.plate_region_detection
        )
        self._set_if_present(
            processor, "cropToPlateRegionEnabled", self._config.crop_to_plate_region
        )

        self._engine = engine
        self._processor = processor

    def _configure_countries(self, engine) -> None:
        total = int(engine.numSupportedCountries)
        for i in range(total):
            engine.set_countryWeight(i, 0.0)

        activated: list[str] = []
        for raw_token in self._config.countries:
            token = str(raw_token).strip()
            if not token:
                continue
            if self._activate_country(engine, token):
                activated.append(token)
            else:
                available = [engine.get_countryCode(i) for i in range(total)]
                raise LprError(
                    f"País/plantilla SimpleLPR no encontrado: '{token}'. "
                    f"Disponibles: {available}"
                )

        if not activated:
            raise LprError(
                "SIMPLE_LPR_COUNTRIES no activó ningún país; revisa la configuración."
            )

        engine.realizeCountryWeights()
        logger.info("SimpleLPR: países/plantillas activos = {}", activated)

    @staticmethod
    def _activate_country(engine, token: str) -> bool:
        """Activa un país por nombre o, si falla, por índice. False si no existe."""
        try:
            engine.set_countryWeight(token, 1.0)
            return True
        except Exception:  # noqa: BLE001 - el binding lanza error nativo al no hallar el nombre
            pass
        try:
            index = int(token)
        except ValueError:
            return False
        try:
            engine.set_countryWeight(index, 1.0)
            return True
        except Exception:  # noqa: BLE001 - índice fuera de rango -> se reporta arriba
            return False

    @staticmethod
    def _set_if_present(obj, attribute: str, value) -> None:
        if hasattr(obj, attribute):
            setattr(obj, attribute, value)

    @staticmethod
    def _log_version(engine) -> None:
        try:
            v = engine.versionNumber
            logger.info("SimpleLPR versión {}.{}.{}.{}", v.A, v.B, v.C, v.D)
        except Exception as exc:  # noqa: BLE001 - solo es un log informativo
            logger.debug("SimpleLPR: no se pudo leer versionNumber: {}", exc)

    # ---- lectura de placa ----

    def read_plate(self, frame_bgr: np.ndarray) -> LprEngineResult:
        matches = self._run_ocr(frame_bgr)
        return self._build_result(matches)

    def _run_ocr(self, frame_bgr: np.ndarray) -> list[tuple[str, float, str]]:
        """Analiza el frame con SimpleLPR. Devuelve (texto_ocr, confianza_0_100, iso)."""
        ok, buffer = cv2.imencode(
            ".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self._config.jpeg_quality]
        )
        if not ok:
            raise LprError("No se pudo codificar el frame para SimpleLPR")

        fd, path = tempfile.mkstemp(suffix=".jpg", prefix="hgac_simplelpr_")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(buffer.tobytes())
            analyze_result = self._processor.analyze(path)
        finally:
            try:
                os.remove(path)
            except OSError as exc:
                logger.debug("SimpleLPR: no se pudo borrar el temporal {}: {}", path, exc)

        return self._parse_matches(analyze_result)

    def _parse_matches(self, analyze_result) -> list[tuple[str, float, str]]:
        """Extrae (texto, confianza_0_100, iso) de la respuesta de analyze().

        Defensivo ante la forma exacta del binding: itera candidatos y sus
        `matches`, leyendo `text`, `confidence` (escala 0-1 en SimpleLPR) y
        `countryISO` con getattr.
        """
        out: list[tuple[str, float, str]] = []
        for candidate in self._iter_candidates(analyze_result):
            for match in self._iter_matches(candidate):
                text = getattr(match, "text", None)
                if not text:
                    continue
                raw_conf = getattr(match, "confidence", 0.0) or 0.0
                # SimpleLPR entrega confianza en 0-1; la llevamos a 0-100.
                conf_100 = float(raw_conf) * 100.0 if float(raw_conf) <= 1.0 else float(raw_conf)
                conf_100 = max(0.0, min(100.0, conf_100))
                iso = getattr(match, "countryISO", "") or getattr(match, "country", "") or ""
                out.append((str(text), conf_100, str(iso)))
        return out

    @staticmethod
    def _iter_candidates(result):
        if result is None:
            return []
        if hasattr(result, "numCandidates_get") and hasattr(result, "candidate_get"):
            return [result.candidate_get(i) for i in range(int(result.numCandidates_get()))]
        candidates = getattr(result, "candidates", None)
        if candidates is not None:
            return list(candidates)
        try:
            return list(result)
        except TypeError:
            return []

    @staticmethod
    def _iter_matches(candidate):
        matches = getattr(candidate, "matches", None)
        if matches is not None:
            return list(matches)
        # Algunos bindings exponen el match directamente en el candidato.
        if hasattr(candidate, "text"):
            return [candidate]
        return []

    # ---- selección y resultado ----

    def _build_result(self, matches: list[tuple[str, float, str]]) -> LprEngineResult:
        if not matches:
            return LprEngineResult(
                best_raw_text=None,
                best_normalized_text=None,
                confidence=0.0,
                plate_crop_jpeg=None,
                candidate_count=0,
                ocr_attempt_count=0,
                preprocessing_variant="simplelpr",
            )

        # Floor de confianza del motor: si hay matches fuertes, los débiles pasan a
        # depuración; si NINGUNO alcanza el floor, se conservan todos para que el
        # servicio pueda marcar LOW_CONFIDENCE (no NO_PLATE_DETECTED).
        floor = self._config.min_confidence
        strong = [m for m in matches if m[1] >= floor]
        pool = strong if strong else matches
        dropped = [m for m in matches if m not in pool]

        entries: list[dict] = []
        attempts = 0
        for ocr_text, conf, iso in pool:
            for cand in generate_candidates(ocr_text):
                attempts += 1
                is_valid, priority = self._classify(cand.text)
                penalty = self._effective_penalty(cand.substitutions)
                reported = max(0.0, conf - penalty)
                score = conf + (_FORMAT_BONUS if is_valid else 0.0) + priority - penalty
                entries.append(
                    {
                        "text": cand.text,
                        "normalized_text": cand.text,
                        "ocr_text": ocr_text,
                        "confidence": round(reported, 1),
                        "score": round(score, 1),
                        "source": cand.source,
                        "substitutions": cand.substitutions,
                        "country_iso": iso,
                        "exceeds_substitution_limit": cand.substitutions
                        > self._config.max_substitutions,
                    }
                )

        best = max(entries, key=lambda item: item["score"])
        rejections = tuple(
            {"ocr_text": text, "confidence": round(conf, 1), "reason": "below_engine_min_confidence"}
            for text, conf, _iso in dropped
        )
        top_scores = tuple(
            sorted(entries, key=lambda item: item["score"], reverse=True)[:_MAX_DEBUG_ITEMS]
        )

        return LprEngineResult(
            best_raw_text=best["text"],
            best_normalized_text=best["text"],
            confidence=round(float(best["confidence"]), 1),
            plate_crop_jpeg=None,  # v1: la evidencia es el frame; recorte de placa = mejora futura
            candidate_count=len(matches),
            ocr_attempt_count=attempts,
            preprocessing_variant=f"simplelpr:{best['source']}",
            selected_roi=best.get("country_iso") or None,
            digit_count=sum(ch.isdigit() for ch in best["text"]),
            alpha_count=sum(ch.isalpha() for ch in best["text"]),
            candidate_rejections=rejections[:_MAX_DEBUG_ITEMS],
            candidate_scores=top_scores,
        )

    def _classify(self, normalized: str) -> tuple[bool, int]:
        """Valida/clasifica contra el catálogo dominicano (autoridad) o el validador."""
        if self._catalog is not None:
            classification = self._catalog.classify(normalized)
            return classification.is_valid, classification.priority
        return self._validator.is_format_valid(normalized), 0

    def _effective_penalty(self, substitutions: int) -> float:
        """Penalización de confianza por sustituciones; doble más allá del límite.

        Una corrección de 1-2 caracteres penaliza poco; una que requiere muchas
        sustituciones cae por debajo del umbral del servicio (LOW_CONFIDENCE),
        sin fabricar la placa.
        """
        base = self._config.substitution_penalty * substitutions
        over = max(0, substitutions - self._config.max_substitutions)
        return base + self._config.substitution_penalty * over
