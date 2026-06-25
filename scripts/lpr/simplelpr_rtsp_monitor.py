#!/usr/bin/env python3
"""Monitor LPR continuo para el PoC HGAC usando SimpleLPR y RTSP."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv
except ImportError:

    def load_dotenv(path: str = ".env") -> bool:
        """Carga un .env basico cuando python-dotenv no esta instalado."""
        env_path = Path(path)
        if not env_path.exists():
            return False
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        return True


DEFAULT_COUNTRIES = "19,74,96"
PLATE_ONE_LETTER = re.compile(r"^[A-Z][0-9]{6}$")
PLATE_TWO_LETTERS = re.compile(r"^[A-Z]{2}[0-9]{5}$")
TRUCK_LABEL = re.compile(r"^[A-Z][0-9]{3}$")
TWO_LETTER_PREFIXES = {"PP", "EX", "DD", "OD", "OZ"}
TO_LETTER = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "4": "A", "6": "G"}
TO_NUMBER = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "B": "8",
    "Z": "2",
    "A": "4",
    "G": "6",
    "T": "7",
}

_shutdown = False


def _stop(_signum, _frame) -> None:
    global _shutdown
    _shutdown = True


def _clean(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def _correct_by_position(text: str, letter_count: int) -> str:
    letters = "".join(TO_LETTER.get(char, char) for char in text[:letter_count])
    numbers = "".join(TO_NUMBER.get(char, char) for char in text[letter_count:])
    return letters + numbers


def normalize_dominican_identifier(text: str) -> tuple[str, str, int]:
    """Devuelve (valor, tipo, correcciones) sin inventar caracteres."""
    raw = _clean(text)
    if len(raw) == 7:
        candidates: list[str] = [raw]
        preferred = 2 if raw[:2] in TWO_LETTER_PREFIXES else 1
        candidates.extend(
            [
                _correct_by_position(raw, preferred),
                _correct_by_position(raw, 3 - preferred),
            ]
        )
        for candidate in dict.fromkeys(candidates):
            if PLATE_ONE_LETTER.fullmatch(candidate) or PLATE_TWO_LETTERS.fullmatch(
                candidate
            ):
                changes = sum(a != b for a, b in zip(raw, candidate))
                return candidate, "PLACA", changes
    elif len(raw) == 4:
        candidate = _correct_by_position(raw, 1)
        if TRUCK_LABEL.fullmatch(candidate):
            changes = sum(a != b for a, b in zip(raw, candidate))
            return candidate, "ROTULO", changes
    return raw, "DESCONOCIDO", 0


def adjusted_confidence(confidence: float, corrections: int, penalty: float) -> float:
    """Reduce confianza cuando la normalizacion corrigio caracteres ambiguos."""
    return max(0.0, min(1.0, float(confidence) - (corrections * penalty)))


def is_recent_duplicate(
    last_published: dict[tuple[str, str], float],
    identifier_type: str,
    identifier: str,
    now: float,
    cooldown_seconds: float,
) -> bool:
    previous = last_published.get((identifier_type, identifier))
    return previous is not None and now - previous < cooldown_seconds


def _atomic_write(path: Path, payload: dict, retries: int = 30) -> None:
    """Reemplaza el JSON sin detener el monitor si Ignition lo tiene abierto."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    last_error: OSError | None = None
    for attempt in range(max(1, retries)):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(min(0.02 * (attempt + 1), 0.20))
        except OSError as exc:
            last_error = exc
            break
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        pass
    raise OSError(
        f"No se pudo publicar {path} despues de {retries} intentos"
    ) from last_error


def _next_trigger(path: Path) -> bool:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        return not bool(previous.get("trigger", False))
    except (OSError, ValueError, TypeError):
        return True


def _read_previous_payload(path: Path) -> dict:
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
        return previous if isinstance(previous, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _event_path(event_dir: Path, payload: dict) -> Path:
    """Genera un nombre sortable para preservar el orden de llegada."""
    timestamp = str(payload["timestamp"]).replace("-", "").replace(":", "")
    timestamp = timestamp.replace(".", "").replace("+", "").replace("Z", "")
    sequence = int(payload.get("event_sequence", 0))
    return event_dir / f"{timestamp}_{sequence:012d}_{uuid.uuid4().hex}.json"


def build_ignition_payload(
    *,
    identifier: str,
    raw_text: str,
    identifier_type: str,
    confidence: float,
    camera_id: str,
    camera_name: str,
    camera_ip: str,
    output_path: Path,
    track_timestamp: float,
) -> dict:
    now = datetime.now(timezone.utc)
    timestamp = now.isoformat().replace("+00:00", "Z")
    is_plate = identifier_type == "PLACA"
    event_id = f"LPR-{camera_id}-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    previous = _read_previous_payload(output_path)
    payload = dict(previous)
    payload.update(
        {
            "timestamp": timestamp,
            "read_timestamp": timestamp,
            "trigger": not bool(previous.get("trigger", False)),
            "status": "PLATE_DETECTED" if is_plate else "ROTULO_DETECTED",
            "event_type": identifier_type,
            "event_sequence": int(previous.get("event_sequence", 0)) + 1,
            "camera_id": camera_id,
            "camera_name": camera_name,
            "camera_ip": camera_ip,
            "frame_path": "",
            "frame_url": "",
            "crop_path": "",
            "crop_url": "",
            "clip_path": "",
            "rejection_reason": "",
            "consensus_votes": 0,
            "consensus_total": 0,
            "consensus_ratio": 0.0,
            "event_id": event_id,
            "engine": "simplelpr_tracker",
            "raw_result": {
                "identifier_type": identifier_type,
                "identifier": identifier,
                "raw_text": raw_text,
                "confidence": round(confidence * 100.0, 1),
                "track_first_detection_seconds": track_timestamp,
            },
        }
    )
    confidence_percent = round(confidence * 100.0, 1)
    if is_plate:
        payload.update(
            {
                "plate": identifier,
                "plate_normalized": identifier,
                "confidence": confidence_percent,
                "plate_confidence": confidence_percent,
                "plate_timestamp": timestamp,
                "plate_event_id": event_id,
                "plate_status": "PLATE_DETECTED",
                "plate_matched": True,
            }
        )
        payload.setdefault("rotulo", "")
        payload.setdefault("rotulo_confidence", 0.0)
        payload.setdefault("rotulo_timestamp", "")
        payload.setdefault("rotulo_event_id", "")
        payload.setdefault("rotulo_status", "")
    else:
        payload.update(
            {
                "rotulo": identifier,
                "rotulo_confidence": confidence_percent,
                "rotulo_timestamp": timestamp,
                "rotulo_event_id": event_id,
                "rotulo_status": "ROTULO_DETECTED",
            }
        )
        payload.setdefault("plate", "")
        payload.setdefault("plate_normalized", "")
        payload.setdefault("confidence", 0.0)
        payload.setdefault("plate_confidence", payload["confidence"])
        payload.setdefault("plate_timestamp", "")
        payload.setdefault("plate_event_id", "")
        payload.setdefault("plate_status", "")
        payload.setdefault("plate_matched", False)
    return payload


def _load_simplelpr():
    try:
        import simplelpr  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "SimpleLPR no esta instalado. Instala el SDK para Python 3.8-3.12."
        ) from exc
    return simplelpr


def _configure_countries(engine, country_ids: str) -> None:
    for index in range(engine.numSupportedCountries):
        engine.set_countryWeight(index, 0.0)
    for token in filter(None, (part.strip() for part in country_ids.split(","))):
        try:
            engine.set_countryWeight(int(token), 1.0)
        except ValueError:
            engine.set_countryWeight(token, 1.0)
    engine.realizeCountryWeights()


def _best_reading(candidate, min_confidence: float, correction_penalty: float):
    matches = list(candidate.matches or [])
    if not matches:
        return None
    raw_matches = [match for match in matches if not match.countryISO]
    match = max(raw_matches or matches, key=lambda item: item.confidence)
    raw = _clean(match.text or "")
    normalized, identifier_type, corrections = normalize_dominican_identifier(raw)
    confidence = adjusted_confidence(match.confidence, corrections, correction_penalty)
    if identifier_type == "DESCONOCIDO" or confidence < min_confidence:
        return None
    return normalized, raw, confidence, identifier_type


def _safe_rtsp_label(url: str) -> str:
    parsed = urlsplit(url)
    return (
        f"{parsed.scheme}://{parsed.hostname or '?'}:{parsed.port or 554}{parsed.path}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Monitor RTSP continuo con SimpleLPR")
    parser.add_argument("--url", default=os.getenv("SIMPLELPR_RTSP_URL", ""))
    parser.add_argument(
        "--camera-id", default=os.getenv("SIMPLELPR_CAMERA_ID", "P1-CARRIL-2")
    )
    parser.add_argument(
        "--camera-name", default=os.getenv("SIMPLELPR_CAMERA_NAME", "P1 - Carril 2")
    )
    parser.add_argument(
        "--countries", default=os.getenv("SIMPLELPR_COUNTRIES", DEFAULT_COUNTRIES)
    )
    parser.add_argument(
        "--product-key", default=os.getenv("SIMPLELPR_PRODUCT_KEY") or None
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=float(os.getenv("SIMPLELPR_MIN_CONFIDENCE", "0.80")),
    )
    parser.add_argument(
        "--correction-penalty",
        type=float,
        default=float(os.getenv("SIMPLELPR_CORRECTION_PENALTY", "0.08")),
    )
    parser.add_argument(
        "--max-width", type=int, default=int(os.getenv("SIMPLELPR_MAX_WIDTH", "1920"))
    )
    parser.add_argument(
        "--max-height", type=int, default=int(os.getenv("SIMPLELPR_MAX_HEIGHT", "1080"))
    )
    parser.add_argument(
        "--max-in-flight",
        type=int,
        default=int(os.getenv("SIMPLELPR_MAX_IN_FLIGHT", "6")),
    )
    parser.add_argument(
        "--frame-stride",
        type=int,
        default=int(os.getenv("SIMPLELPR_FRAME_STRIDE", "1")),
    )
    parser.add_argument(
        "--tracker-window",
        type=float,
        default=float(os.getenv("SIMPLELPR_TRACKER_WINDOW", "1.5")),
    )
    parser.add_argument(
        "--tracker-idle",
        type=float,
        default=float(os.getenv("SIMPLELPR_TRACKER_IDLE", "0.75")),
    )
    parser.add_argument(
        "--tracker-min-frames",
        type=int,
        default=int(os.getenv("SIMPLELPR_TRACKER_MIN_FRAMES", "2")),
    )
    parser.add_argument(
        "--dedup-seconds",
        type=float,
        default=float(os.getenv("SIMPLELPR_DEDUP_SECONDS", "30")),
    )
    parser.add_argument(
        "--output",
        default=os.getenv("IGNITION_LPR_LATEST_PATH", "C:/Users/Public/hgac_lpr.json"),
    )
    parser.add_argument(
        "--event-dir",
        default=os.getenv("IGNITION_LPR_EVENT_DIR", "C:/Users/Public/hgac_lpr_events"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.url:
        print("Falta SIMPLELPR_RTSP_URL o --url.", file=sys.stderr)
        return 2
    simplelpr = _load_simplelpr()
    setup = simplelpr.EngineSetupParms()
    setup.cudaDeviceId = -1
    setup.enableImageProcessingWithGPU = False
    setup.enableClassificationWithGPU = False
    setup.maxConcurrentImageProcessingOps = 0
    engine = simplelpr.SimpleLPR(setup)
    if args.product_key:
        engine.set_productKey(args.product_key)
    _configure_countries(engine, args.countries)
    pool = engine.createProcessorPool(setup.maxConcurrentImageProcessingOps)
    pool.plateRegionDetectionEnabled = True
    pool.cropToPlateRegionEnabled = False
    tracker = engine.createPlateCandidateTracker(
        simplelpr.PlateCandidateTrackerSetupParms(
            triggerWindowInSec=max(0.5, args.tracker_window),
            maxIdleTimeInSec=max(0.25, args.tracker_idle),
            minTriggerFrameCount=max(1, args.tracker_min_frames),
            thumbnailWidth=256,
            thumbnailHeight=128,
        )
    )
    try:
        video = engine.openVideoSource(
            args.url,
            simplelpr.FrameFormat.FRAME_FORMAT_BGR24,
            args.max_width,
            args.max_height,
        )
    except RuntimeError as exc:
        print(
            f"No se pudo abrir RTSP {_safe_rtsp_label(args.url)}: {exc}. "
            "Verifica VPN, puerto 554, IP y credenciales.",
            file=sys.stderr,
        )
        return 3
    if video.state != simplelpr.VideoSourceState.VIDEO_SOURCE_STATE_OPEN:
        print(f"No se pudo abrir la camara. Estado: {video.state}", file=sys.stderr)
        return 3

    output_path = Path(args.output)
    event_dir = Path(args.event_dir)
    camera_ip = urlsplit(args.url).hostname or ""
    pending: dict[int, object] = {}
    pending_order: deque[int] = deque()
    completed_results: dict[int, object] = {}
    max_in_flight = max(1, args.max_in_flight)
    frame_stride = max(1, args.frame_stride)
    frame_count = 0
    submitted_count = 0
    dropped_count = 0
    duplicate_count = 0
    result_count = 0
    last_published: dict[tuple[str, str], float] = {}
    heartbeat_at = time.monotonic()

    def publish_tracker_result(tracker_result) -> None:
        nonlocal duplicate_count
        tracks = sorted(
            tracker_result.newTracks,
            key=lambda item: float(item.firstDetectionTimestamp),
        )
        for track in tracks:
            reading = _best_reading(
                track.representativeCandidate,
                args.min_confidence,
                args.correction_penalty,
            )
            if not reading:
                continue
            identifier, raw, confidence, identifier_type = reading
            published_at = time.monotonic()
            if is_recent_duplicate(
                last_published,
                identifier_type,
                identifier,
                published_at,
                max(0.0, args.dedup_seconds),
            ):
                duplicate_count += 1
                continue
            payload = build_ignition_payload(
                identifier=identifier,
                raw_text=raw,
                identifier_type=identifier_type,
                confidence=confidence,
                camera_id=args.camera_id,
                camera_name=args.camera_name,
                camera_ip=camera_ip,
                output_path=output_path,
                track_timestamp=float(track.firstDetectionTimestamp),
            )
            try:
                _atomic_write(output_path, payload)
                _atomic_write(_event_path(event_dir, payload), payload, retries=3)
            except OSError as exc:
                print(f"[WARN] Evento LPR no publicado completamente: {exc}")
                continue
            last_published[(identifier_type, identifier)] = published_at
            print(
                f"[{identifier_type:<6}] {identifier:<10} conf={confidence:.2f} "
                f"camara={args.camera_id} -> {output_path}"
            )

    def collect_available_results() -> int:
        collected = 0
        while True:
            result = pool.pollNextResult(0, simplelpr.TIMEOUT_IMMEDIATE)
            if result is None:
                break
            collected += 1
            result_id = getattr(result, "requestId", None)
            if result_id is None and pending_order:
                result_id = pending_order[0]
            if result_id is not None:
                completed_results[int(result_id)] = result
        while pending_order and pending_order[0] in completed_results:
            ordered_id = pending_order.popleft()
            ordered_result = completed_results.pop(ordered_id)
            analyzed_frame = pending.pop(ordered_id, None)
            if analyzed_frame is None or ordered_result.errorInfo:
                continue
            publish_tracker_result(
                tracker.processFrameCandidates(ordered_result, analyzed_frame)
            )
        return collected

    print(f"SimpleLPR HGAC | camara={args.camera_id} | {_safe_rtsp_label(args.url)}")
    print(
        "Monitoreando continuamente. "
        f"Tracker={args.tracker_window:g}s/{args.tracker_idle:g}s, "
        f"cola={max_in_flight}, stride={frame_stride}. Ctrl+C para detener."
    )
    try:
        while not _shutdown:
            frame = video.nextFrame()
            if frame is None:
                if video.isLiveSource:
                    video.reconnect()
                    time.sleep(0.2)
                    continue
                break
            frame_count += 1
            result_count += collect_available_results()
            if time.monotonic() - heartbeat_at >= 10.0:
                print(
                    f"[LPR] vivo | frames={frame_count} enviados={submitted_count} "
                    f"resultados={result_count} descartados={dropped_count} "
                    f"duplicados={duplicate_count} en_cola={len(pending)}"
                )
                heartbeat_at = time.monotonic()
            if frame_count % frame_stride != 0:
                continue
            if len(pending) >= max_in_flight:
                dropped_count += 1
                continue
            request_id = int(frame.sequenceNumber)
            pending[request_id] = frame
            pending_order.append(request_id)
            pool.launchAnalyze(
                streamId=0,
                requestId=request_id,
                timeoutInMs=simplelpr.TIMEOUT_INFINITE,
                frame=frame,
            )
            submitted_count += 1
            result_count += collect_available_results()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            publish_tracker_result(tracker.flush())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _stop)
    raise SystemExit(main())
