"""Orquestación de una lectura LPR.

Flujo:
1. Pide un frame a `CameraService.capture_current_frame` (reutiliza el último
   frame del stream si está activo; nunca abre la cámara directamente).
2. Guarda SIEMPRE el frame analizado como evidencia.
3. Decodifica y ejecuta el motor LPR.
4. Normaliza y valida la placa.
5. Decide el estado (PLATE_DETECTED / LOW_CONFIDENCE / NO_PLATE_DETECTED / ERROR).
6. Guarda el recorte de placa solo si hubo detección.
7. Devuelve un resultado estructurado con tiempos y URLs públicas.

Los errores de cámara (no existe / no entrega frame) se propagan para que la
capa HTTP los mapee a 404/503; no se transforman en evidencia.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone

import cv2
import numpy as np
from loguru import logger

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.modules.camera.camera_service import CameraService
from app.modules.lpr.domain.plate_ambiguity import detect_ambiguity
from app.modules.lpr.domain.plate_classification import PlateClassification
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.lpr_models import LprReadRequest, LprReadResponse, LprReadStatus
from app.modules.lpr.lpr_result_storage import LprResultStorage, StoredEvidence
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateValidator

_CATALOG_EXPECTED_FORMAT = "DOMINICAN_PLATE_CATALOG"


class LprService:
    def __init__(
        self,
        camera_service: CameraService,
        engine: LprEngine,
        storage: LprResultStorage,
        normalizer: PlateNormalizer,
        validator: PlateValidator,
        min_confidence: float = 70.0,
        max_processing_ms: int = 5000,
        catalog: DominicanPlatePatternCatalog | None = None,
        ambiguous_min_score_delta: float = 15.0,
        ambiguous_candidate_distance: int = 1,
        require_multiframe_confirmation: bool = False,
        result_observer: Callable[[LprReadResponse], None] | None = None,
    ) -> None:
        self._camera = camera_service
        self._engine = engine
        self._storage = storage
        self._normalizer = normalizer
        self._validator = validator
        self._min_confidence = min_confidence
        self._max_processing_ms = max_processing_ms
        # Observador opcional invocado con CADA lectura (aceptada o rechazada). Se
        # usa para publicar el "latest" en Ignition sin acoplar el servicio al
        # escritor concreto. Sus fallos NUNCA deben romper la respuesta LPR.
        self._result_observer = result_observer
        # Catálogo dominicano opcional: si es None, el comportamiento es el legacy
        # (solo PlateValidator por regex). Si se inyecta, manda en format_valid y
        # aporta clasificación + detección de ambigüedad.
        self._catalog = catalog
        self._ambiguous_min_score_delta = ambiguous_min_score_delta
        self._ambiguous_candidate_distance = ambiguous_candidate_distance
        # Preparado para exigir confirmación multi-frame; aún no altera la decisión.
        self._require_multiframe_confirmation = require_multiframe_confirmation

    def read_plate(self, request: LprReadRequest) -> LprReadResponse:
        started = time.monotonic()
        detected_at = datetime.now(timezone.utc)
        event_id = request.event_id or f"LPR-{detected_at.strftime('%Y%m%d-%H%M%S')}"

        # 1. Frame desde Camera. Propaga CameraNotFoundError (->404) y
        #    CameraError (->503): no se atrapan aquí a propósito.
        frame_bytes = self._camera.capture_current_frame(request.camera_id)

        # 2. El frame analizado siempre se guarda como evidencia.
        stored_frame = self._storage.save_frame(frame_bytes, detected_at)

        image = _decode_jpeg(frame_bytes)
        if image is None:
            logger.error("LPR {}: no se pudo decodificar el frame", event_id)
            return self._respond(
                event_id=event_id,
                request=request,
                started=started,
                detected_at=detected_at,
                stored_frame=stored_frame,
                status=LprReadStatus.ERROR,
                rejection_reason="decode_error",
            )

        # 3. Motor LPR. Un fallo del motor (p.ej. EasyOCR ausente) no debe tumbar
        #    el endpoint: se devuelve estado ERROR con el frame ya guardado.
        try:
            engine_result = self._engine.read_plate(image)
        except Exception:  # noqa: BLE001 - el motor PoC puede fallar de varias formas
            logger.exception("LPR {}: el motor de lectura falló", event_id)
            return self._respond(
                event_id=event_id,
                request=request,
                started=started,
                detected_at=detected_at,
                stored_frame=stored_frame,
                status=LprReadStatus.ERROR,
                rejection_reason="engine_error",
            )

        # 4. Sin texto candidato -> NO_PLATE_DETECTED.
        if engine_result.best_raw_text is None:
            logger.info("LPR {}: sin placa detectada", event_id)
            return self._respond(
                event_id=event_id,
                request=request,
                started=started,
                detected_at=detected_at,
                stored_frame=stored_frame,
                status=LprReadStatus.NO_PLATE_DETECTED,
                rejection_reason="no_text",
                engine_result=engine_result,
            )

        # 5. Hay un candidato. Se guarda el crop usado para OCR (haya o no
        #    formato válido) porque hubo una región/lectura.
        normalized = self._normalizer.normalize(engine_result.best_raw_text)
        confidence = engine_result.confidence

        # Clasificación dominicana (si el catálogo está activo). El catálogo manda
        # en format_valid; sin catálogo, se mantiene la validación por regex legacy.
        classification = self._catalog.classify(normalized) if self._catalog else None
        format_valid = (
            classification.is_valid
            if classification is not None
            else self._validator.is_format_valid(normalized)
        )
        enriched_scores = self._enrich_candidate_scores(engine_result.candidate_scores)

        stored_crop: StoredEvidence | None = None
        if engine_result.plate_crop_jpeg is not None:
            stored_crop = self._storage.save_crop(
                engine_result.plate_crop_jpeg, detected_at
            )

        # 6. Decisión de aceptación. Precedencia: confianza -> formato -> ambigüedad.
        #    El candidato rechazado NO se expone como `plate`; no se infiere ni
        #    autocompleta ningún carácter (p.ej. G237627 NO se "corrige" a G737627).
        if confidence < self._min_confidence:
            status = LprReadStatus.LOW_CONFIDENCE
            rejection_reason: str | None = "low_confidence"
        elif not format_valid:
            status = LprReadStatus.FORMAT_MISMATCH
            rejection_reason = "format_mismatch"
        else:
            ambiguity = detect_ambiguity(
                enriched_scores,
                min_score_delta=self._ambiguous_min_score_delta,
                max_distance=self._ambiguous_candidate_distance,
            )
            if ambiguity.is_ambiguous:
                status = LprReadStatus.AMBIGUOUS_READ
                rejection_reason = ambiguity.reason
                logger.info(
                    "LPR {}: lectura ambigua entre {} (delta de score < {})",
                    event_id,
                    ambiguity.candidates,
                    self._ambiguous_min_score_delta,
                )
            else:
                status = LprReadStatus.PLATE_DETECTED
                rejection_reason = None

        accepted = status is LprReadStatus.PLATE_DETECTED
        logger.info(
            "LPR {}: {} candidato='{}' conf={:.1f} format_valid={} tipo={} [terminal={} lane={}]",
            event_id,
            status.value,
            normalized,
            confidence,
            format_valid,
            classification.code if classification else "n/a",
            request.terminal,
            request.lane,
        )
        return self._respond(
            event_id=event_id,
            request=request,
            started=started,
            detected_at=detected_at,
            stored_frame=stored_frame,
            status=status,
            rejection_reason=rejection_reason,
            plate=engine_result.best_raw_text if accepted else None,
            plate_normalized=normalized if accepted else None,
            confidence=confidence,
            stored_crop=stored_crop,
            format_valid=format_valid,
            engine_result=engine_result,
            classification=classification,
            candidate_scores=enriched_scores,
        )

    def _enrich_candidate_scores(self, scores: tuple[dict, ...]) -> list[dict]:
        """Añade clasificación DGII a cada candidato del motor (si el catálogo está on).

        El motor entrega solo hechos OCR; aquí (capa de dominio/servicio) se agregan
        `format_valid`, `plate_type`, `vehicle_type`, `pattern_priority` y
        `rejection_reason`. Sin catálogo, se devuelven los scores tal cual.
        """
        if self._catalog is None:
            return [dict(score) for score in scores]

        enriched: list[dict] = []
        for score in scores:
            normalized = str(score.get("normalized_text") or score.get("text") or "")
            classification = self._catalog.classify(normalized)
            entry = dict(score)
            entry["format_valid"] = classification.is_valid
            entry["plate_type"] = classification.code
            entry["vehicle_type"] = classification.vehicle_type
            entry["pattern_priority"] = classification.priority
            entry["rejection_reason"] = (
                None if classification.is_valid else "format_mismatch"
            )
            enriched.append(entry)
        return enriched

    def _respond(
        self,
        *,
        event_id: str,
        request: LprReadRequest,
        started: float,
        detected_at: datetime,
        stored_frame: StoredEvidence,
        status: LprReadStatus,
        rejection_reason: str | None,
        plate: str | None = None,
        plate_normalized: str | None = None,
        confidence: float = 0.0,
        stored_crop: StoredEvidence | None = None,
        format_valid: bool = False,
        engine_result: LprEngineResult | None = None,
        classification: PlateClassification | None = None,
        candidate_scores: list[dict] | None = None,
    ) -> LprReadResponse:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > self._max_processing_ms:
            logger.warning(
                "LPR {}: procesamiento {}ms supera el máximo {}ms",
                event_id,
                elapsed_ms,
                self._max_processing_ms,
            )
        response = LprReadResponse(
            event_id=event_id,
            camera_id=request.camera_id,
            status=status,
            plate=plate,
            plate_normalized=plate_normalized,
            confidence=confidence,
            source_frame_path=stored_frame.path,
            source_frame_url=stored_frame.url,
            plate_crop_path=stored_crop.path if stored_crop else None,
            plate_crop_url=stored_crop.url if stored_crop else None,
            processing_time_ms=elapsed_ms,
            detected_at=detected_at,
            engine=self._engine.name,
            candidate_count=engine_result.candidate_count if engine_result else 0,
            ocr_attempt_count=engine_result.ocr_attempt_count if engine_result else 0,
            best_raw_text=engine_result.best_raw_text if engine_result else None,
            best_normalized_text=(
                engine_result.best_normalized_text if engine_result else None
            ),
            expected_format=(
                _CATALOG_EXPECTED_FORMAT if self._catalog else self._validator.expected_format
            ),
            format_valid=format_valid,
            rejection_reason=rejection_reason,
            plate_type=classification.code if classification else None,
            vehicle_type=classification.vehicle_type if classification else None,
            format_pattern=(
                classification.pattern if classification and classification.pattern else None
            ),
            preprocessing_variant=(
                engine_result.preprocessing_variant if engine_result else None
            ),
            crop_saved=stored_crop is not None,
            selected_roi=engine_result.selected_roi if engine_result else None,
            digit_count=engine_result.digit_count if engine_result else 0,
            alpha_count=engine_result.alpha_count if engine_result else 0,
            candidate_rejections=(
                list(engine_result.candidate_rejections) if engine_result else []
            ),
            candidate_scores=(
                candidate_scores
                if candidate_scores is not None
                else (list(engine_result.candidate_scores) if engine_result else [])
            ),
        )
        self._notify_observer(response)
        return response

    def _notify_observer(self, response: LprReadResponse) -> None:
        """Publica la lectura al observador (p.ej. Ignition latest), si hay uno.

        Aísla cualquier fallo (archivo bloqueado por Ignition, disco lleno, etc.)
        para que la respuesta LPR se devuelva siempre.
        """
        if self._result_observer is None:
            return
        try:
            self._result_observer(response)
        except Exception:  # noqa: BLE001 - publicar el latest no debe romper la lectura
            logger.exception(
                "LPR {}: no se pudo publicar la lectura al observador",
                response.event_id,
            )


def _decode_jpeg(frame_bytes: bytes) -> np.ndarray | None:
    array = np.frombuffer(frame_bytes, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)
