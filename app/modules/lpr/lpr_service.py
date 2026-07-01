"""Orquestación de una lectura LPR (placa + rótulo, multi-motor, multiframe).

Dos modos, según `burst_frame_count`:

- **Single-frame** (`count <= 1`): un frame; decisión confianza→formato→ambigüedad.
  Es el comportamiento histórico (compatibilidad de contrato y tests).
- **Ráfaga multiframe** (`count > 1`): captura una ráfaga (~1-2 s del paso del
  vehículo), puntúa la calidad del ROI de cada frame (nitidez/brillo), procesa los
  mejores N con el/los motor(es) y decide por **consenso** (votos entre frames +
  confianza). Pensado para placas en movimiento, donde un solo frame puede salir
  borroso.

En ambos modos: el frame guardado como evidencia es el MEJOR frame; el OCR corre
sobre el recorte `lpr_roi`; el rótulo se lee aparte sobre `rotulo_roi`. SimpleLPR/
EasyOCR solo detectan/OCR; la AUTORIDAD de formato es el catálogo dominicano.

Los errores de cámara (no existe / no entrega frame) se propagan para que la capa
HTTP los mapee a 404/503; no se transforman en evidencia.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import cv2
import numpy as np
from loguru import logger

from app.integrations.lpr.lpr_engine import LprEngine, LprEngineResult
from app.modules.camera.camera_service import CameraService
from app.modules.camera.camera_registry import CameraConfig
from app.modules.lpr.domain.plate_ambiguity import detect_ambiguity
from app.modules.lpr.domain.plate_classification import PlateClassification
from app.modules.lpr.domain.plate_pattern_catalog import DominicanPlatePatternCatalog
from app.modules.lpr.frame_quality import FrameQuality, assess_quality, rank_frames
from app.modules.lpr.lpr_models import (
    LprReadRequest,
    LprReadResponse,
    LprReadStatus,
    RotuloReadStatus,
)
from app.modules.lpr.lpr_result_storage import LprResultStorage, StoredEvidence
from app.modules.lpr.plate_normalizer import PlateNormalizer
from app.modules.lpr.plate_validator import PlateValidator
from app.modules.lpr.roi_evidence import clamp_roi, encode_jpeg, render_overlay


_CATALOG_EXPECTED_FORMAT = "DOMINICAN_PLATE_CATALOG"

_REJECTION_BY_STATUS: dict[LprReadStatus, str | None] = {
    LprReadStatus.PLATE_DETECTED: None,
    LprReadStatus.LOW_CONFIDENCE: "low_confidence",
    LprReadStatus.FORMAT_MISMATCH: "format_mismatch",
    LprReadStatus.NO_PLATE_DETECTED: "no_text",
    LprReadStatus.BLURRY_FRAME: "blurry_frame",
}

# Máximo de candidatos de placa expuestos en la respuesta (depuración acotada).
_MAX_RESPONSE_CANDIDATES = 12


@dataclass
class FrameCapture:
    """Un frame de la ráfaga: bytes, imagen decodificada, ROI de placa y calidad."""

    index: int
    frame_bytes: bytes
    image: np.ndarray
    captured_at: datetime
    plate_crop: np.ndarray | None = None
    plate_roi: dict | None = None
    quality: FrameQuality | None = None


@dataclass
class _PlateOutcome:
    """Resultado de leer la PLACA (single-frame o consenso multiframe)."""

    status: LprReadStatus
    rejection_reason: str | None
    engine_result: LprEngineResult | None = None
    engine_label: str = ""
    plate_engine: str | None = None
    attempts: list[dict] = field(default_factory=list)
    plate: str | None = None
    plate_normalized: str | None = None
    confidence: float = 0.0
    format_valid: bool = False
    classification: PlateClassification | None = None
    enriched_scores: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    # Métricas de la ráfaga (single-frame => count=1).
    burst_frame_count: int = 1
    processed_frame_count: int = 0
    usable_frame_count: int = 0
    best_frame_index: int | None = None
    best_frame_sharpness: float = 0.0
    best_frame_brightness: float = 0.0
    consensus_votes: int = 0
    consensus_total: int = 0


@dataclass
class _RotuloOutcome:
    """Resultado de leer el RÓTULO (independiente de la placa)."""

    status: RotuloReadStatus
    rotulo: str | None = None
    rotulo_normalized: str | None = None
    confidence: float = 0.0
    format_valid: bool = False
    rejection_reason: str | None = None
    engine: str | None = None
    candidates: list[dict] = field(default_factory=list)
    crop_jpeg: bytes | None = None


@dataclass
class _Consensus:
    """Salida del consenso multiframe."""

    status: LprReadStatus
    accepted: bool
    winner_group: dict | None
    votes: int
    total: int


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
        # --- Multi-motor (auto/fallback) ---
        fallback_engine: LprEngine | None = None,
        engine_mode: str | None = None,
        unavailable_engines: list[dict] | None = None,
        # --- Rótulo de camión (lectura independiente) ---
        rotulo_engine: LprEngine | None = None,
        rotulo_validator: PlateValidator | None = None,
        rotulo_min_confidence: float = 60.0,
        # --- Evidencia de depuración (recortes de ROI + overlay) ---
        save_debug_evidence: bool = True,
        evidence_jpeg_quality: int = 90,
        # --- Ráfaga multiframe + consenso ---
        # count <= 1 => single-frame (comportamiento histórico). Producción usa 12.
        burst_frame_count: int = 1,
        burst_interval_ms: int = 0,
        burst_top_frames: int = 5,
        min_frame_sharpness: float = 80.0,
        min_frame_brightness: float = 30.0,
        max_frame_brightness: float = 235.0,
        consensus_min_votes: int = 2,
        single_frame_accept_confidence: float = 75.0,
        save_burst_frames: bool = False,
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
        self._fallback_engine = fallback_engine
        self._engine_mode = engine_mode
        self._unavailable_engines = list(unavailable_engines or [])
        self._rotulo_engine = rotulo_engine
        self._rotulo_validator = rotulo_validator
        self._rotulo_min_confidence = rotulo_min_confidence
        self._save_debug_evidence = save_debug_evidence
        self._evidence_jpeg_quality = evidence_jpeg_quality
        self._burst_frame_count = max(1, burst_frame_count)
        self._burst_interval_ms = max(0, burst_interval_ms)
        self._burst_top_frames = max(1, burst_top_frames)
        self._min_frame_sharpness = min_frame_sharpness
        self._min_frame_brightness = min_frame_brightness
        self._max_frame_brightness = max_frame_brightness
        self._consensus_min_votes = max(1, consensus_min_votes)
        self._single_frame_accept_confidence = single_frame_accept_confidence
        self._save_burst_frames = save_burst_frames

    # ================================================================
    # Punto de entrada: elige single-frame o ráfaga
    # ================================================================

    def read_plate(self, request: LprReadRequest) -> LprReadResponse:
        started = time.monotonic()
        detected_at = datetime.now(timezone.utc)
        event_id = request.event_id or f"LPR-{detected_at.strftime('%Y%m%d-%H%M%S')}"
        if self._burst_frame_count and self._burst_frame_count > 1:
            return self._read_plate_burst(request, started, detected_at, event_id)
        return self._read_plate_single(request, started, detected_at, event_id)

    # ================================================================
    # Modo single-frame (histórico)
    # ================================================================

    def _read_plate_single(
        self,
        request: LprReadRequest,
        started: float,
        detected_at: datetime,
        event_id: str,
    ) -> LprReadResponse:
        frame_bytes = self._camera.capture_current_frame(request.camera_id)
        stored_frame = self._storage.save_frame(frame_bytes, detected_at)

        image = _decode_jpeg(frame_bytes)
        if image is None:
            logger.error("LPR {}: no se pudo decodificar el frame", event_id)
            return self._build_response(
                event_id=event_id,
                request=request,
                started=started,
                detected_at=detected_at,
                stored_frame=stored_frame,
                plate=_PlateOutcome(
                    status=LprReadStatus.ERROR,
                    rejection_reason="decode_error",
                    engine_label=self._engine.name,
                ),
                rotulo=_RotuloOutcome(status=RotuloReadStatus.NOT_CONFIGURED),
            )

        camera_config = self._camera.get_config(request.camera_id)
        plate_image, camera_roi = _apply_camera_lpr_roi(image, camera_config)
        rotulo_image, rotulo_roi = clamp_roi(
            image,
            camera_config.rotulo_roi_x,
            camera_config.rotulo_roi_y,
            camera_config.rotulo_roi_width,
            camera_config.rotulo_roi_height,
        )
        if camera_roi is not None:
            logger.info(
                "LPR {}: ROI placa {} sobre frame {}x{}",
                event_id,
                camera_roi,
                image.shape[1],
                image.shape[0],
            )

        plate = self._read_plate_outcome(plate_image, event_id, request)
        quality = assess_quality(
            plate_image,
            min_sharpness=self._min_frame_sharpness,
            min_brightness=self._min_frame_brightness,
            max_brightness=self._max_frame_brightness,
        )
        plate.burst_frame_count = 1
        plate.processed_frame_count = 1
        plate.usable_frame_count = 1 if quality.usable else 0
        plate.best_frame_index = 0
        plate.best_frame_sharpness = quality.sharpness
        plate.best_frame_brightness = quality.brightness

        rotulo = self._read_rotulo_outcome(rotulo_image, rotulo_roi, event_id)
        evidence = self._save_debug(
            detected_at=detected_at,
            full_frame=image,
            plate_crop=plate_image if camera_roi is not None else None,
            plate_roi=camera_roi,
            rotulo_crop=rotulo_image if rotulo_roi is not None else None,
            rotulo_roi=rotulo_roi,
            plate_label=plate.plate or plate.plate_normalized,
            rotulo_label=rotulo.rotulo,
        )
        return self._build_response(
            event_id=event_id,
            request=request,
            started=started,
            detected_at=detected_at,
            stored_frame=stored_frame,
            plate=plate,
            rotulo=rotulo,
            camera_roi=camera_roi,
            rotulo_roi=rotulo_roi,
            evidence=evidence,
        )

    # ================================================================
    # Modo ráfaga multiframe + consenso
    # ================================================================

    def _read_plate_burst(
        self,
        request: LprReadRequest,
        started: float,
        detected_at: datetime,
        event_id: str,
    ) -> LprReadResponse:
        captures = self._capture_burst(
            request.camera_id, self._burst_frame_count, self._burst_interval_ms, detected_at
        )
        if not captures:
            # Ningún frame decodificable: cae al camino single-frame (guarda frame
            # y devuelve ERROR decode_error de forma controlada).
            logger.warning("LPR {}: ráfaga sin frames decodificables", event_id)
            return self._read_plate_single(request, started, detected_at, event_id)

        camera_config = self._camera.get_config(request.camera_id)

        # 1. Calidad del ROI de placa por frame (no del frame completo).
        for cap in captures:
            crop, roi = _apply_camera_lpr_roi(cap.image, camera_config)
            cap.plate_crop = crop
            cap.plate_roi = roi
            cap.quality = assess_quality(
                crop,
                min_sharpness=self._min_frame_sharpness,
                min_brightness=self._min_frame_brightness,
                max_brightness=self._max_frame_brightness,
            )

        usable = [c for c in captures if c.quality and c.quality.usable]
        ranked = rank_frames(usable if usable else captures)
        top = ranked[: self._burst_top_frames]
        logger.info(
            "LPR {}: ráfaga {} frames, {} utilizables, procesando top {}",
            event_id,
            len(captures),
            len(usable),
            len(top),
        )

        # 2. OCR sobre los top frames; candidatos etiquetados con frame + calidad.
        all_candidates: list[dict] = []
        per_frame_attempts: list[list[dict]] = []
        reads_by_index: dict[int, tuple[FrameCapture, LprEngineResult | None, str | None]] = {}
        processed_caps: list[FrameCapture] = []
        for cap in top:
            processed_caps.append(cap)
            adopted, name, attempts = self._run_engines_on_image(cap.plate_crop, event_id)
            per_frame_attempts.append(attempts)
            reads_by_index[cap.index] = (cap, adopted, name)
            if adopted is not None and adopted.best_raw_text is not None:
                all_candidates.extend(self._frame_candidates(adopted, name, cap))
            if not usable and self._has_minimum_consensus(all_candidates):
                break

        engine_attempts = list(self._unavailable_engines) + self._merge_engine_attempts(
            per_frame_attempts
        )

        # 3. Consenso.
        processed_count = len(processed_caps)
        cons = self._select_best_plate_candidate(all_candidates, processed_count)
        status = cons.status
        outcome_accepted = cons.accepted
        rejection_reason = _REJECTION_BY_STATUS.get(status)
        if cons.accepted and cons.votes < self._consensus_min_votes and not usable:
            status = LprReadStatus.LOW_CONFIDENCE
            outcome_accepted = False
            rejection_reason = "insufficient_consensus"
        elif (
            not cons.accepted
            and status is LprReadStatus.LOW_CONFIDENCE
            and cons.votes < self._consensus_min_votes
        ):
            rejection_reason = "insufficient_consensus"
        if not cons.accepted and not usable:
            # Ningún frame utilizable: lectura no confiable.
            status = LprReadStatus.BLURRY_FRAME
            rejection_reason = _REJECTION_BY_STATUS.get(status)

        # 4. Frame de evidencia: el del ganador si se aceptó; si no, el de mayor calidad.
        winner = cons.winner_group
        if outcome_accepted and winner is not None:
            best_index = winner["representative"].get("frame_index")
        else:
            best_index = processed_caps[0].index if processed_caps else None
        best_cap, best_er, best_name = reads_by_index.get(
            best_index, (processed_caps[0] if processed_caps else captures[0], None, None)
        )

        normalized_winner = winner["normalized_text"] if winner else None
        classification = (
            self._catalog.classify(normalized_winner)
            if self._catalog and normalized_winner
            else None
        )
        outcome = _PlateOutcome(
            status=status,
            rejection_reason=rejection_reason,
            engine_result=best_er,
            engine_label=self._engine_label(best_name or (winner["representative"]["engine"] if winner else None)),
            plate_engine=best_name or (winner["representative"]["engine"] if winner else self._engine.name),
            attempts=engine_attempts,
            plate=(winner["representative"]["raw_text"] if outcome_accepted and winner else None),
            plate_normalized=(normalized_winner if outcome_accepted else None),
            confidence=(winner["max_confidence"] if winner else 0.0),
            format_valid=(winner["format_valid"] if winner else False),
            classification=classification,
            candidates=self._rank_candidates_for_response(all_candidates),
            burst_frame_count=len(captures),
            processed_frame_count=processed_count,
            usable_frame_count=len(usable),
            best_frame_index=best_cap.index if best_cap else None,
            best_frame_sharpness=best_cap.quality.sharpness if best_cap and best_cap.quality else 0.0,
            best_frame_brightness=best_cap.quality.brightness if best_cap and best_cap.quality else 0.0,
            consensus_votes=cons.votes,
            consensus_total=cons.total,
        )
        logger.info(
            "LPR {}: consenso {} placa='{}' votos={}/{} conf={:.1f} [terminal={} lane={}]",
            event_id,
            status.value,
            outcome.plate_normalized,
            cons.votes,
            cons.total,
            outcome.confidence,
            request.terminal,
            request.lane,
        )

        # 5. Rótulo sobre el mejor frame.
        rotulo_image, rotulo_roi = clamp_roi(
            best_cap.image,
            camera_config.rotulo_roi_x,
            camera_config.rotulo_roi_y,
            camera_config.rotulo_roi_width,
            camera_config.rotulo_roi_height,
        )
        rotulo = self._read_rotulo_outcome(rotulo_image, rotulo_roi, event_id)

        # 6. Evidencia del mejor frame.
        stored_frame = self._storage.save_frame(best_cap.frame_bytes, detected_at)
        evidence = self._save_debug(
            detected_at=detected_at,
            full_frame=best_cap.image,
            plate_crop=best_cap.plate_crop if best_cap.plate_roi is not None else None,
            plate_roi=best_cap.plate_roi,
            rotulo_crop=rotulo_image if rotulo_roi is not None else None,
            rotulo_roi=rotulo_roi,
            plate_label=outcome.plate or normalized_winner,
            rotulo_label=rotulo.rotulo,
        )
        burst_urls = self._save_burst_frame_urls(processed_caps, detected_at)

        return self._build_response(
            event_id=event_id,
            request=request,
            started=started,
            detected_at=detected_at,
            stored_frame=stored_frame,
            plate=outcome,
            rotulo=rotulo,
            camera_roi=best_cap.plate_roi,
            rotulo_roi=rotulo_roi,
            evidence=evidence,
            burst_frame_urls=burst_urls,
        )

    def _capture_burst(
        self, camera_id: str, count: int, interval_ms: int, base_time: datetime
    ) -> list[FrameCapture]:
        """Captura una ráfaga y decodifica cada frame. `captured_at` es aproximado
        (base + índice*intervalo). Propaga errores de cámara (404/503)."""
        raw_frames = self._grab_burst_bytes(camera_id, count, interval_ms)
        captures: list[FrameCapture] = []
        for index, frame_bytes in enumerate(raw_frames):
            image = _decode_jpeg(frame_bytes)
            if image is None:
                continue
            captures.append(
                FrameCapture(
                    index=index,
                    frame_bytes=frame_bytes,
                    image=image,
                    captured_at=base_time + timedelta(milliseconds=index * max(0, interval_ms)),
                )
            )
        return captures

    def _grab_burst_bytes(self, camera_id: str, count: int, interval_ms: int) -> list[bytes]:
        """Usa `capture_frame_burst` (una sola sesión RTSP) si existe; si no, hace
        un bucle de `capture_current_frame` con sleep controlado."""
        burst = getattr(self._camera, "capture_frame_burst", None)
        if callable(burst):
            return burst(camera_id, count=count, interval_ms=interval_ms)
        frames: list[bytes] = []
        for i in range(count):
            frames.append(self._camera.capture_current_frame(camera_id))
            if i + 1 < count and interval_ms > 0:
                time.sleep(interval_ms / 1000.0)
        return frames

    def _frame_candidates(
        self, engine_result: LprEngineResult, engine_name: str | None, cap: FrameCapture
    ) -> list[dict]:
        """Candidatos de un frame, etiquetados con índice y calidad, para el consenso."""
        name = engine_name or self._engine.name
        normalized_best = (
            self._normalizer.normalize(engine_result.best_raw_text)
            if engine_result.best_raw_text
            else None
        )
        enriched = self._enrich_candidate_scores(engine_result.candidate_scores)
        candidates = self._plate_candidates(
            engine_result, name, enriched, self._is_format_valid(normalized_best)
        )
        quality = cap.quality
        for candidate in candidates:
            if not candidate.get("normalized_text") and candidate.get("raw_text"):
                candidate["normalized_text"] = self._normalizer.normalize(candidate["raw_text"])
            candidate["format_valid"] = self._is_format_valid(candidate.get("normalized_text"))
            candidate["frame_index"] = cap.index
            candidate["frame_quality_score"] = quality.score if quality else None
            candidate["sharpness"] = quality.sharpness if quality else None
            candidate["brightness"] = quality.brightness if quality else None
        return candidates

    def _select_best_plate_candidate(
        self, candidates: list[dict], processed_count: int
    ) -> _Consensus:
        """Agrupa por placa normalizada, puntúa por votos+confianza+calidad y decide.

        Aceptación: (format_valid y votos >= min_votes y max_conf >= min_confidence)
        o (format_valid y max_conf >= single_frame_accept_confidence).
        """
        groups: dict[str, list[dict]] = {}
        for candidate in candidates:
            normalized = candidate.get("normalized_text")
            if not normalized:
                continue
            groups.setdefault(normalized, []).append(candidate)

        if not groups:
            return _Consensus(
                status=LprReadStatus.NO_PLATE_DETECTED,
                accepted=False,
                winner_group=None,
                votes=0,
                total=processed_count,
            )

        scored: list[dict] = []
        for normalized, items in groups.items():
            confidences = [float(i.get("confidence") or 0.0) for i in items]
            frames = {i.get("frame_index") for i in items if i.get("frame_index") is not None}
            votes = len(frames) if frames else len(items)
            max_conf = max(confidences)
            avg_conf = sum(confidences) / len(confidences)
            best_quality = max(
                (float(i.get("frame_quality_score") or 0.0) for i in items), default=0.0
            )
            representative = max(items, key=lambda i: float(i.get("confidence") or 0.0))
            score = (
                max_conf * 0.55
                + avg_conf * 0.25
                + min(votes, 3) * 10.0
                + best_quality * 20.0
            )
            scored.append(
                {
                    "normalized_text": normalized,
                    "votes": votes,
                    "max_confidence": round(max_conf, 1),
                    "avg_confidence": round(avg_conf, 1),
                    "best_frame_quality": round(best_quality, 3),
                    "format_valid": self._is_format_valid(normalized),
                    "score": round(score, 1),
                    "representative": representative,
                }
            )

        scored.sort(key=lambda g: g["score"], reverse=True)
        valid = [g for g in scored if g["format_valid"]]
        consensus_valid = [
            group for group in valid if group["votes"] >= self._consensus_min_votes
        ]
        winner = (consensus_valid or valid or scored)[0]

        accepted = False
        if winner["format_valid"]:
            if (
                winner["votes"] >= self._consensus_min_votes
                and winner["max_confidence"] >= self._min_confidence
            ):
                accepted = True
            elif winner["max_confidence"] >= self._single_frame_accept_confidence:
                accepted = True

        if accepted:
            status = LprReadStatus.PLATE_DETECTED
        elif winner["format_valid"]:
            status = LprReadStatus.LOW_CONFIDENCE
        else:
            status = LprReadStatus.FORMAT_MISMATCH

        return _Consensus(
            status=status,
            accepted=accepted,
            winner_group=winner,
            votes=winner["votes"],
            total=processed_count,
        )

    def _has_minimum_consensus(self, candidates: list[dict]) -> bool:
        groups: dict[str, list[dict]] = {}
        for candidate in candidates:
            normalized = candidate.get("normalized_text")
            if normalized:
                groups.setdefault(normalized, []).append(candidate)
        for normalized, items in groups.items():
            frames = {item.get("frame_index") for item in items}
            votes = len(frames)
            max_confidence = max(
                (float(item.get("confidence") or 0.0) for item in items),
                default=0.0,
            )
            if (
                votes >= self._consensus_min_votes
                and max_confidence >= self._min_confidence
                and self._is_format_valid(normalized)
            ):
                return True
        return False

    @staticmethod
    def _rank_candidates_for_response(candidates: list[dict]) -> list[dict]:
        """Ordena candidatos (válidos y de mayor confianza primero) y acota la lista."""
        ranked = sorted(
            candidates,
            key=lambda c: (bool(c.get("format_valid")), float(c.get("confidence") or 0.0)),
            reverse=True,
        )
        return ranked[:_MAX_RESPONSE_CANDIDATES]

    def _save_burst_frame_urls(
        self, top: list[FrameCapture], detected_at: datetime
    ) -> list[str]:
        """Guarda los top frames de la ráfaga (opcional; off por defecto)."""
        if not self._save_burst_frames:
            return []
        urls: list[str] = []
        try:
            for cap in top:
                stored = self._storage.save(
                    "burst", cap.frame_bytes, detected_at, f"burst_{cap.index:02d}"
                )
                urls.append(stored.url)
        except Exception:  # noqa: BLE001 - la evidencia de ráfaga no debe romper la respuesta
            logger.exception("LPR: no se pudieron guardar los frames de la ráfaga")
        return urls

    def _merge_engine_attempts(self, per_frame_attempts: list[list[dict]]) -> list[dict]:
        """Fusiona los intentos por-frame en un resumen por motor (OK domina)."""
        order = [self._engine.name] + (
            [self._fallback_engine.name] if self._fallback_engine is not None else []
        )
        priority = {"OK": 3, "NO_DETECTION": 2, "ERROR": 1, "NOT_USED": 0}
        best: dict[str, str] = {}
        for attempts in per_frame_attempts:
            for attempt in attempts:
                engine = attempt["engine"]
                status = attempt["status"]
                if engine not in best or priority.get(status, 0) > priority.get(best[engine], 0):
                    best[engine] = status
        merged = [{"engine": engine, "status": best.get(engine, "NOT_USED")} for engine in order]
        # Motores vistos fuera del orden esperado (defensivo).
        for engine, status in best.items():
            if engine not in order:
                merged.append({"engine": engine, "status": status})
        return merged

    # ================================================================
    # Motores de PLACA (compartido por single-frame y ráfaga)
    # ================================================================

    def _run_engines_on_image(
        self, image: np.ndarray, event_id: str
    ) -> tuple[LprEngineResult | None, str | None, list[dict]]:
        """Corre el motor primario y, si no detecta o falla, el fallback.

        Devuelve (resultado_adoptado, nombre_motor, intentos). No incluye los
        motores no disponibles en build-time (los añade el llamador)."""
        engines = [self._engine] + (
            [self._fallback_engine] if self._fallback_engine is not None else []
        )
        attempts: list[dict] = []
        adopted: LprEngineResult | None = None
        adopted_name: str | None = None
        for index, eng in enumerate(engines):
            try:
                result = eng.read_plate(image)
            except Exception as exc:  # noqa: BLE001 - el motor PoC puede fallar de varias formas
                logger.exception("LPR {}: motor {} falló", event_id, eng.name)
                attempts.append({"engine": eng.name, "status": "ERROR", "error": str(exc)})
                continue
            if result.best_raw_text is None:
                attempts.append({"engine": eng.name, "status": "NO_DETECTION"})
                if adopted is None:
                    adopted, adopted_name = result, eng.name
                continue
            attempts.append({"engine": eng.name, "status": "OK"})
            adopted, adopted_name = result, eng.name
            for rest in engines[index + 1 :]:
                attempts.append({"engine": rest.name, "status": "NOT_USED"})
            break
        return adopted, adopted_name, attempts

    def _engine_label(self, adopted_name: str | None) -> str:
        primary = self._engine.name
        if adopted_name and adopted_name != primary:
            return f"{primary}+{adopted_name}_fallback"
        return adopted_name or (self._engine_mode or primary)

    def _read_plate_outcome(
        self, plate_image: np.ndarray, event_id: str, request: LprReadRequest
    ) -> _PlateOutcome:
        adopted, adopted_name, attempts = self._run_engines_on_image(plate_image, event_id)
        attempts = list(self._unavailable_engines) + attempts
        engine_label = self._engine_label(adopted_name)

        if adopted is None:
            return _PlateOutcome(
                status=LprReadStatus.ERROR,
                rejection_reason="engine_error",
                engine_label=engine_label,
                plate_engine=self._engine.name,
                attempts=attempts,
            )
        if adopted.best_raw_text is None:
            return _PlateOutcome(
                status=LprReadStatus.NO_PLATE_DETECTED,
                rejection_reason="no_text",
                engine_result=adopted,
                engine_label=engine_label,
                plate_engine=adopted_name,
                attempts=attempts,
                candidates=self._plate_candidates(adopted, adopted_name, [], False),
            )
        return self._decide_plate(adopted, adopted_name, engine_label, attempts, event_id, request)

    def _decide_plate(
        self,
        engine_result: LprEngineResult,
        engine_name: str,
        engine_label: str,
        attempts: list[dict],
        event_id: str,
        request: LprReadRequest,
    ) -> _PlateOutcome:
        """Decisión single-frame. Precedencia: confianza -> formato -> ambigüedad.

        El candidato rechazado NO se expone como `plate`; no se infiere ni
        autocompleta ningún carácter (p.ej. G237627 NO se "corrige" a G737627).
        """
        normalized = (
            engine_result.best_normalized_text
            or self._normalizer.normalize(engine_result.best_raw_text)
        )
        confidence = engine_result.confidence

        classification = self._catalog.classify(normalized) if self._catalog else None
        format_valid = (
            classification.is_valid
            if classification is not None
            else self._validator.is_format_valid(normalized)
        )
        enriched_scores = self._enrich_candidate_scores(engine_result.candidate_scores)
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
            "LPR {}: {} candidato='{}' conf={:.1f} format_valid={} tipo={} motor={} [terminal={} lane={}]",
            event_id,
            status.value,
            normalized,
            confidence,
            format_valid,
            classification.code if classification else "n/a",
            engine_name,
            request.terminal,
            request.lane,
        )
        return _PlateOutcome(
            status=status,
            rejection_reason=rejection_reason,
            engine_result=engine_result,
            engine_label=engine_label,
            plate_engine=engine_name,
            attempts=attempts,
            plate=engine_result.best_raw_text if accepted else None,
            plate_normalized=normalized if accepted else None,
            confidence=confidence,
            format_valid=format_valid,
            classification=classification,
            enriched_scores=enriched_scores,
            candidates=self._plate_candidates(
                engine_result, engine_name, enriched_scores, format_valid
            ),
        )

    def _is_format_valid(self, normalized: str | None) -> bool:
        if not normalized:
            return False
        if self._catalog is not None:
            return self._catalog.classify(normalized).is_valid
        return self._validator.is_format_valid(normalized)

    def _plate_candidates(
        self,
        engine_result: LprEngineResult,
        engine_name: str,
        enriched_scores: list[dict],
        best_format_valid: bool,
    ) -> list[dict]:
        """Candidatos OCR (crudos/normalizados/rechazados) para depuración.

        Se exponen AUNQUE no haya placa aceptada, para ver qué leyó el motor.
        """
        candidates: list[dict] = []
        for score in enriched_scores:
            candidates.append(
                {
                    "engine": engine_name,
                    "raw_text": score.get("ocr_text") or score.get("text"),
                    "normalized_text": score.get("normalized_text") or score.get("text"),
                    "confidence": float(score.get("confidence", 0.0) or 0.0),
                    "format_valid": bool(score.get("format_valid", False)),
                    "source": score.get("source"),
                    "substitutions": score.get("substitutions"),
                    "rejection_reason": score.get("rejection_reason"),
                }
            )
        # Si el motor no entregó scores detallados pero sí un mejor texto, lo
        # exponemos igual (caso del motor OpenCV con un único candidato).
        if not candidates and engine_result.best_raw_text:
            candidates.append(
                {
                    "engine": engine_name,
                    "raw_text": engine_result.best_raw_text,
                    "normalized_text": engine_result.best_normalized_text,
                    "confidence": float(engine_result.confidence or 0.0),
                    "format_valid": bool(best_format_valid),
                }
            )
        for rej in engine_result.candidate_rejections or ():
            candidates.append(
                {
                    "engine": engine_name,
                    "raw_text": rej.get("ocr_text") or rej.get("text"),
                    "confidence": float(rej.get("confidence", 0.0) or 0.0),
                    "format_valid": False,
                    "rejection_reason": rej.get("reason") or rej.get("rejection_reason"),
                }
            )
        return candidates

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

    # ================================================================
    # Lectura de RÓTULO
    # ================================================================

    def _read_rotulo_outcome(
        self, rotulo_image: np.ndarray, rotulo_roi: dict | None, event_id: str
    ) -> _RotuloOutcome:
        """Lee el rótulo sobre `rotulo_roi` con su validador propio.

        Si la cámara no define rótulo o no hay motor de rótulo, devuelve
        NOT_CONFIGURED sin tocar OCR. SimpleLPR es para placas; el rótulo usa el
        motor OpenCV/EasyOCR inyectado.
        """
        if (
            rotulo_roi is None
            or self._rotulo_engine is None
            or self._rotulo_validator is None
        ):
            return _RotuloOutcome(status=RotuloReadStatus.NOT_CONFIGURED)

        engine_name = self._rotulo_engine.name
        try:
            result = self._rotulo_engine.read_plate(rotulo_image)
        except Exception as exc:  # noqa: BLE001 - el motor de rótulo no debe tumbar la lectura
            logger.exception("LPR {}: motor de rótulo {} falló", event_id, engine_name)
            return _RotuloOutcome(
                status=RotuloReadStatus.ERROR,
                rejection_reason="engine_error",
                engine=engine_name,
                candidates=[{"engine": engine_name, "rejection_reason": str(exc)}],
            )

        if result.best_raw_text is None:
            return _RotuloOutcome(
                status=RotuloReadStatus.NO_ROTULO_DETECTED,
                rejection_reason="no_text",
                engine=engine_name,
            )

        normalized = self._normalizer.normalize(result.best_raw_text)
        format_valid = self._rotulo_validator.is_format_valid(normalized)
        confidence = result.confidence
        candidates = self._rotulo_candidates(result, engine_name, normalized, format_valid)

        if confidence < self._rotulo_min_confidence:
            status = RotuloReadStatus.LOW_CONFIDENCE
            rejection_reason: str | None = "low_confidence"
        elif not format_valid:
            status = RotuloReadStatus.FORMAT_MISMATCH
            rejection_reason = "format_mismatch"
        else:
            status = RotuloReadStatus.ROTULO_DETECTED
            rejection_reason = None
        accepted = status is RotuloReadStatus.ROTULO_DETECTED

        logger.info(
            "LPR {}: rótulo {} candidato='{}' conf={:.1f} format_valid={}",
            event_id,
            status.value,
            normalized,
            confidence,
            format_valid,
        )
        return _RotuloOutcome(
            status=status,
            rotulo=result.best_raw_text if accepted else None,
            rotulo_normalized=normalized if accepted else None,
            confidence=confidence,
            format_valid=format_valid,
            rejection_reason=rejection_reason,
            engine=engine_name,
            candidates=candidates,
            crop_jpeg=result.plate_crop_jpeg,
        )

    @staticmethod
    def _rotulo_candidates(
        result: LprEngineResult, engine_name: str, normalized: str, format_valid: bool
    ) -> list[dict]:
        candidates: list[dict] = []
        for score in result.candidate_scores or ():
            candidates.append(
                {
                    "engine": engine_name,
                    "raw_text": score.get("ocr_text") or score.get("text"),
                    "normalized_text": score.get("normalized_text") or score.get("text"),
                    "confidence": float(score.get("confidence", 0.0) or 0.0),
                    "format_valid": bool(score.get("format_valid", False)),
                }
            )
        if not candidates:
            candidates.append(
                {
                    "engine": engine_name,
                    "raw_text": result.best_raw_text,
                    "normalized_text": normalized,
                    "confidence": float(result.confidence or 0.0),
                    "format_valid": bool(format_valid),
                }
            )
        return candidates

    # ================================================================
    # Evidencia de depuración
    # ================================================================

    def _save_debug(
        self,
        *,
        detected_at: datetime,
        full_frame: np.ndarray,
        plate_crop: np.ndarray | None,
        plate_roi: dict | None,
        rotulo_crop: np.ndarray | None,
        rotulo_roi: dict | None,
        plate_label: str | None,
        rotulo_label: str | None,
    ) -> dict:
        """Guarda recortes de ROI + overlay. Nunca rompe la lectura.

        Solo escribe cuando hay algún ROI configurado (para no generar archivos
        ni alterar el flujo cuando la cámara procesa el frame completo).
        """
        urls: dict = {
            "plate_roi_url": None,
            "rotulo_roi_url": None,
            "roi_overlay_url": None,
        }
        if not self._save_debug_evidence or not (plate_roi or rotulo_roi):
            return urls
        quality = self._evidence_jpeg_quality
        try:
            if plate_crop is not None:
                jpeg = encode_jpeg(plate_crop, quality)
                if jpeg:
                    urls["plate_roi_url"] = self._storage.save(
                        "roi", jpeg, detected_at, "plate_roi"
                    ).url
            if rotulo_crop is not None:
                jpeg = encode_jpeg(rotulo_crop, quality)
                if jpeg:
                    urls["rotulo_roi_url"] = self._storage.save(
                        "roi", jpeg, detected_at, "rotulo_roi"
                    ).url
            overlay = render_overlay(
                full_frame,
                plate_roi=plate_roi,
                rotulo_roi=rotulo_roi,
                plate_label=plate_label,
                rotulo_label=rotulo_label,
                quality=max(60, quality - 5),
            )
            if overlay:
                urls["roi_overlay_url"] = self._storage.save(
                    "overlay", overlay, detected_at, "overlay"
                ).url
        except Exception:  # noqa: BLE001 - la evidencia de debug nunca rompe la respuesta
            logger.exception("LPR: no se pudo guardar evidencia de depuración")
        return urls

    # ================================================================
    # Construcción de la respuesta
    # ================================================================

    def _build_response(
        self,
        *,
        event_id: str,
        request: LprReadRequest,
        started: float,
        detected_at: datetime,
        stored_frame: StoredEvidence,
        plate: _PlateOutcome,
        rotulo: _RotuloOutcome,
        camera_roi: dict | None = None,
        rotulo_roi: dict | None = None,
        evidence: dict | None = None,
        burst_frame_urls: list[str] | None = None,
    ) -> LprReadResponse:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > self._max_processing_ms:
            logger.warning(
                "LPR {}: procesamiento {}ms supera el máximo {}ms",
                event_id,
                elapsed_ms,
                self._max_processing_ms,
            )

        engine_result = plate.engine_result
        classification = plate.classification
        evidence = evidence or {}
        camera_config = self._camera.get_config(request.camera_id)
        camera_ip = ""
        if camera_config.source_type == "rtsp":
            camera_ip = urlsplit(camera_config.source).hostname or ""

        # El crop de placa se guarda SOLO si hubo candidato (texto): sin texto, el
        # servicio fuerza no-crop aunque el motor haya entregado bytes.
        stored_crop: StoredEvidence | None = None
        if (
            engine_result is not None
            and engine_result.best_raw_text is not None
            and engine_result.plate_crop_jpeg is not None
        ):
            stored_crop = self._storage.save_crop(engine_result.plate_crop_jpeg, detected_at)

        rotulo_crop: StoredEvidence | None = None
        if rotulo.crop_jpeg is not None:
            rotulo_crop = self._storage.save("crops", rotulo.crop_jpeg, detected_at, "rotulo_crop")

        candidate_scores = (
            plate.enriched_scores
            if plate.enriched_scores
            else (list(engine_result.candidate_scores) if engine_result else [])
        )
        consensus_ratio = (
            round(plate.consensus_votes / plate.consensus_total, 3)
            if plate.consensus_total
            else 0.0
        )
        frame_candidates = [
            {
                "frame_index": candidate.get("frame_index"),
                "text": candidate.get("normalized_text"),
                "raw_text": candidate.get("raw_text"),
                "confidence": candidate.get("confidence", 0.0),
                "format_valid": candidate.get("format_valid", False),
                "frame_quality_score": candidate.get("frame_quality_score"),
                "sharpness": candidate.get("sharpness"),
                "brightness": candidate.get("brightness"),
            }
            for candidate in plate.candidates
            if candidate.get("frame_index") is not None
        ]
        frames_requested = self._burst_frame_count if self._burst_frame_count > 1 else 1
        frames_captured = plate.burst_frame_count or frames_requested
        frames_processed = plate.processed_frame_count or (
            1 if engine_result is not None else 0
        )

        response = LprReadResponse(
            event_id=event_id,
            camera_id=request.camera_id,
            camera_name=camera_config.camera_name,
            camera_ip=camera_ip,
            status=plate.status,
            plate=plate.plate,
            plate_normalized=plate.plate_normalized,
            confidence=plate.confidence,
            source_frame_path=stored_frame.path,
            source_frame_url=stored_frame.url,
            plate_crop_path=stored_crop.path if stored_crop else None,
            plate_crop_url=stored_crop.url if stored_crop else None,
            processing_time_ms=elapsed_ms,
            detected_at=detected_at,
            engine=plate.engine_label or self._engine.name,
            candidate_count=engine_result.candidate_count if engine_result else 0,
            ocr_attempt_count=engine_result.ocr_attempt_count if engine_result else 0,
            best_raw_text=engine_result.best_raw_text if engine_result else None,
            best_normalized_text=(
                engine_result.best_normalized_text if engine_result else None
            ),
            expected_format=(
                _CATALOG_EXPECTED_FORMAT if self._catalog else self._validator.expected_format
            ),
            format_valid=plate.format_valid,
            rejection_reason=plate.rejection_reason,
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
            camera_roi=camera_roi,
            digit_count=engine_result.digit_count if engine_result else 0,
            alpha_count=engine_result.alpha_count if engine_result else 0,
            candidate_rejections=(
                list(engine_result.candidate_rejections) if engine_result else []
            ),
            candidate_scores=candidate_scores,
            frames_requested=frames_requested,
            frames_captured=frames_captured,
            frames_processed=frames_processed,
            consensus_votes=plate.consensus_votes,
            consensus_total=plate.consensus_total,
            consensus_ratio=consensus_ratio,
            frame_candidates=frame_candidates,
            # --- Contrato extendido ---
            plate_status=plate.status,
            plate_engine=plate.plate_engine,
            plate_candidates=plate.candidates,
            rotulo=rotulo.rotulo,
            rotulo_normalized=rotulo.rotulo_normalized,
            rotulo_confidence=rotulo.confidence,
            rotulo_status=rotulo.status,
            rotulo_engine=rotulo.engine,
            rotulo_format_valid=rotulo.format_valid,
            rotulo_rejection_reason=rotulo.rejection_reason,
            rotulo_candidates=rotulo.candidates,
            rotulo_roi=rotulo_roi,
            rotulo_crop_path=rotulo_crop.path if rotulo_crop else None,
            rotulo_crop_url=rotulo_crop.url if rotulo_crop else None,
            engine_attempts=plate.attempts,
            debug_frame_url=stored_frame.url,
            plate_roi_url=evidence.get("plate_roi_url"),
            rotulo_roi_url=evidence.get("rotulo_roi_url"),
            roi_overlay_url=evidence.get("roi_overlay_url"),
            # --- Ráfaga multiframe ---
            burst_frame_count=plate.burst_frame_count,
            processed_frame_count=plate.processed_frame_count,
            usable_frame_count=plate.usable_frame_count,
            best_frame_index=plate.best_frame_index,
            best_frame_sharpness=plate.best_frame_sharpness,
            best_frame_brightness=plate.best_frame_brightness,
            burst_frame_urls=burst_frame_urls or [],
        )
        self._notify_observer(response)
        return response

    def _notify_observer(self, response: LprReadResponse) -> None:
        """Publica la lectura al observador, si hay uno, sin romper la respuesta."""
        if self._result_observer is None:
            return
        try:
            self._result_observer(response)
        except Exception:  # noqa: BLE001 - publicar latest no debe romper LPR
            logger.exception(
                "LPR {}: no se pudo publicar la lectura al observador",
                response.event_id,
            )


def _decode_jpeg(frame_bytes: bytes) -> np.ndarray | None:
    array = np.frombuffer(frame_bytes, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _apply_camera_lpr_roi(
    image: np.ndarray, camera_config: CameraConfig
) -> tuple[np.ndarray, dict | None]:
    """Recorta `image` a la ROI de PLACA de la cámara, acotada al tamaño del frame.

    Delega en `roi_evidence.clamp_roi`. Devuelve ``(imagen_para_ocr, roi_aplicado)``:
    - Sin ROI configurado: el frame completo y ``None``.
    - Con ROI: el recorte (vista NumPy) y el dict del ROI realmente aplicado.
    - ROI fuera del frame: el frame completo y ``None``.

    No modifica `image`: la evidencia del frame completo ya se guardó antes.
    """
    return clamp_roi(
        image,
        camera_config.roi_x,
        camera_config.roi_y,
        camera_config.roi_width,
        camera_config.roi_height,
    )
