"""Serialized YOLO/RF-DETR inference with bounded camera mailboxes."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import os
import threading
import time
from typing import Callable

import numpy as np

from .classes import RULE_CLASSES, RULE_LABELS, canonical_name
from .incidents import IncidentCoordinator
from .inference import LEGACY_MODEL_PATH, RFDETRDetector, model_name, resolve_model_path
from .ppe import (BoxDetection, Track, ViolationTracker, draw_detections,
                  event_image, missing_equipment)


MODEL_PATH = LEGACY_MODEL_PATH
_KINDS = {rule: "no_" + rule for rule in RULE_CLASSES}


@dataclass(frozen=True)
class Analysis:
    key: tuple
    frame: np.ndarray
    status: str
    violations: int = 0
    kind: str = "no_helmet"
    # None preserves the old single-kind contract; () explicitly means no event.
    violation_counts: tuple[tuple[str, int], ...] | None = None
    on_recorded: Callable[[str], None] | None = None


@dataclass(frozen=True)
class Detection:
    frame: np.ndarray
    status: str
    violations: int
    kind: str = "no_helmet"
    violation_counts: tuple[tuple[str, int], ...] | None = None
    observations: tuple | None = None
    boxes: tuple[BoxDetection, ...] = ()
    required: tuple[str, ...] = ()

    def __iter__(self):
        # Preserve the image/status interface used by the local application.
        yield self.frame
        yield self.status


class PPEDetector:
    """Load one backend lazily; a model is never called concurrently."""

    def __init__(self, path: str | Path = MODEL_PATH, *, backend: str | None = None) -> None:
        self.path = resolve_model_path(path)
        self.backend = backend or ("yolo" if self.path == LEGACY_MODEL_PATH else "rfdetr")
        self._model = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(self.path.name)
        if self.backend == "rfdetr":
            self._model = RFDETRDetector(self.path)
            return
        from ultralytics import YOLO
        import torch

        cpu_threads = max(1, min(2, (os.cpu_count() or 2) // 2))
        torch.set_num_threads(cpu_threads)
        self._model = YOLO(str(self.path), task="detect")
        self._model.add_callback("on_predict_start", lambda _: torch.set_num_threads(cpu_threads))

    def analyze(self, frame: np.ndarray, required: tuple[str, ...] = ("helmet",)) -> Detection:
        required = tuple(rule for rule in RULE_CLASSES if rule in required)
        with self._lock:
            if self._model is None:
                self._load()
            if self.backend == "yolo":
                return self._analyze_yolo(frame, required)
            return self._analyze_rfdetr(frame, required)

    def _analyze_yolo(self, frame, required) -> Detection:
        result = self._model.predict(frame, conf=0.4, imgsz=640, verbose=False)[0]
        items = result.names.items() if isinstance(result.names, dict) else enumerate(result.names)
        names = {int(key): canonical_name(value) for key, value in items}
        if not any(name in {"PERSON", "HELMET", "NO HELMET"} for name in names.values()):
            raise ValueError("Модель YOLO не содержит классов контроля каски.")
        # Local YOLO uses direct negative classes only. No absence is guessed.
        counts = []
        for rule in required:
            negative_ids = [key for key, value in names.items() if value == "NO " + RULE_CLASSES[rule]]
            count = sum(int((result.boxes.cls == key).sum().item()) for key in negative_ids)
            if count:
                counts.append((_KINDS[rule], count))
        violations = sum(count for _, count in counts)
        if required == ("helmet",):
            status = f"Без каски: {violations}" if violations else "Без каски: не обнаружено"
        else:
            status = f"СИЗ: нарушений {violations}" if required else "Контроль СИЗ выключен"
        status += f" · YOLO: объектов {len(result.boxes.cls)}"
        unsupported = [RULE_LABELS[rule] for rule in required
                       if "NO " + RULE_CLASSES[rule] not in names.values()]
        if unsupported:
            status += " · Нет контроля: " + ", ".join(unsupported)
        return Detection(result.plot(), status, violations,
                         counts[0][0] if counts else "no_helmet", tuple(counts))

    def _analyze_rfdetr(self, frame, required) -> Detection:
        prediction = self._model.predict(frame)
        height, width = frame.shape[:2]
        boxes = []
        for box, confidence, class_id in zip(prediction.xyxy, prediction.confidence,
                                             prediction.class_id, strict=True):
            name = self._model.label(int(class_id))
            if name is None or not np.isfinite(box).all() or not np.isfinite(confidence):
                continue
            x1, y1, x2, y2 = map(float, box)
            clipped = (max(0, x1), max(0, y1), min(width, x2), min(height, y2))
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                boxes.append(BoxDetection(name, float(confidence), clipped))
        verified = self._model.labels_verified
        supported = self._model.supported_rules if verified else ()
        active = tuple(rule for rule in required if rule in supported)
        observations = missing_equipment(boxes, active, frame.shape, imgsz=self._model.resolution)
        status = f"{model_name(self.path)} · RF-DETR · {self._model.device.upper()} · объектов {len(boxes)}"
        if not verified:
            status += " · Нет полной карты классов: журнал выключен"
        elif unsupported := [RULE_LABELS[rule] for rule in required if rule not in supported]:
            status += " · Нет контроля: " + ", ".join(unsupported)
        return Detection(draw_detections(frame, boxes), status, 0, violation_counts=(),
                         observations=tuple(observations), boxes=tuple(boxes), required=active)


class HelmetDetector(PPEDetector):
    """Compatibility entry point for the original local YOLO model."""

    def __init__(self, path: str | Path = MODEL_PATH) -> None:
        super().__init__(path, backend="yolo")


_PREDICT_LOCK = threading.Lock()
_CACHED_DETECTOR: PPEDetector | None = None


def _analyze_shared(path, frame, required):
    # All cameras share one loaded model. Switching releases the previous one.
    global _CACHED_DETECTOR
    with _PREDICT_LOCK:
        if _CACHED_DETECTOR is None or _CACHED_DETECTOR.path != path:
            _CACHED_DETECTOR = PPEDetector(path)
        return _CACHED_DETECTOR.analyze(frame, required)


class AnalysisWorker:
    """At most one pending frame; model/rule changes invalidate old evidence."""

    def __init__(self, detector=None) -> None:
        self._detector = detector
        self._condition = threading.Condition()
        self._pending = None
        self._result: Analysis | None = None
        self._closed = False
        self._requested_context = None
        self._tracking_context = None
        self._acknowledgements = []
        self._tracker = ViolationTracker()
        self._incidents = IncidentCoordinator()
        self._thread = threading.Thread(target=self._run, name="SafetyMonitorAnalysis", daemon=True)
        self._thread.start()

    def submit(self, key: tuple, frame: np.ndarray, required=("helmet",), model_path=MODEL_PATH) -> None:
        required = tuple(rule for rule in RULE_CLASSES if rule in required)
        path = resolve_model_path(model_path)
        context = (key[:-1], required, path)
        with self._condition:
            if not self._closed:
                if context != self._requested_context:
                    self._result = None
                    self._acknowledgements.clear()
                self._requested_context = context
                self._pending = key, frame, required, path, context, time.monotonic()
                self._condition.notify()

    def snapshot(self) -> Analysis | None:
        with self._condition:
            return self._result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = self._result = None
            self._acknowledgements.clear()
            self._condition.notify()

    def _acknowledge(self, context, people_by_kind, kind):
        # PostgreSQL's writer only queues the acknowledgement. Tracking remains
        # owned by this worker thread, and old camera generations cannot affect it.
        with self._condition:
            if not self._closed and self._requested_context == context:
                self._acknowledgements.append((context, people_by_kind.get(kind, ()), kind))

    def _confirm(self, detection, frame, context, captured):
        if self._tracking_context != context:
            self._tracking_context = context
            self._tracker = ViolationTracker()
            self._incidents = IncidentCoordinator()
        with self._condition:
            acknowledgements, self._acknowledgements = self._acknowledgements, []
        for ack_context, ids, kind in acknowledgements:
            if ack_context != context:
                continue
            rule = next((rule for rule, candidate in _KINDS.items() if candidate == kind), None)
            for person_id, episode in ids:
                person = self._incidents.people.get(person_id)
                if (person is not None and rule in person.rules
                        and person.rules[rule].episode is episode):
                    self._incidents.mark_logged(person, (rule,))
        self._tracker.update(detection.observations, captured)
        self._incidents.begin_frame(captured)
        counts = Counter()
        people_by_kind = {}
        any_missing = False
        for track, missing in self._tracker.observed:
            any_missing |= bool(missing)
            clear = set()
            if track.kind == "PERSON":
                x1, y1, x2, y2 = track.box
                for item in detection.boxes:
                    cx, cy = (item.box[0] + item.box[2]) / 2, (item.box[1] + item.box[3]) / 2
                    if item.confidence >= .5 and x1 <= cx <= x2 and y1 <= cy <= y2:
                        clear.update(rule for rule in detection.required if RULE_CLASSES[rule] == item.name)
            person, confirmed = self._incidents.observe(
                "camera", 0, "workspace", track, missing, clear, frame, captured)
            for rule in confirmed:
                kind = _KINDS[rule]
                counts[kind] += 1
                people_by_kind.setdefault(kind, []).append((person.id, person.rules[rule].episode))
        status = detection.status
        if counts:
            status += " · Подтверждено: " + ", ".join(
                f"{RULE_LABELS[rule]} {counts[_KINDS[rule]]}" for rule in RULE_CLASSES if counts[_KINDS[rule]])
        elif any_missing:
            status += " · Наблюдение СИЗ: подтверждение 30 с"
        else:
            status += " · Нарушений не подтверждено"
        callback = (lambda kind: self._acknowledge(context, people_by_kind, kind)) if counts else None
        return tuple(counts.items()), status, callback

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                key, frame, required, path, context, captured = self._pending
                self._pending = None
            violations, kind, counts, callback = 0, "no_helmet", (), None
            failed = False
            try:
                if self._detector is None:
                    detection = _analyze_shared(path, frame, required)
                elif isinstance(self._detector, PPEDetector):
                    detection = self._detector.analyze(frame, required)
                else:
                    detection = self._detector.analyze(frame)
                annotated, status = detection
                violations = getattr(detection, "violations", 0)
                kind = getattr(detection, "kind", "no_helmet")
                counts = getattr(detection, "violation_counts", None)
                if getattr(detection, "observations", None) is not None:
                    counts, status, callback = self._confirm(detection, frame, context, captured)
                    violations = sum(count for _, count in counts)
                    kind = counts[0][0] if counts else "no_helmet"
            except ModuleNotFoundError:
                dependency_file = "requirements.txt" if path == LEGACY_MODEL_PATH else "requirements-rfdetr.txt"
                annotated, status = frame, f"Анализ недоступен: установите зависимости из {dependency_file}"
                failed = True
            except FileNotFoundError:
                name = self._detector.path.name if isinstance(self._detector, PPEDetector) else path.name
                annotated, status = frame, f"Анализ недоступен: файл {name} не найден"
                failed = True
            except ValueError as exc:
                annotated, status = frame, f"Ошибка модели {path.name}: {str(exc)[:150]}"
                failed = True
            except Exception:
                annotated, status = frame, f"Ошибка анализа {path.name}: проверьте модель и зависимости"
                failed = True
            if failed:
                # An inference outage breaks consecutive evidence, even if a
                # frame resumes inside the coordinator's normal capture gap.
                self._tracking_context = None
                violations, counts, callback = 0, (), None
            with self._condition:
                if self._closed:
                    return
                if context == self._requested_context:
                    self._result = Analysis(key, annotated, status, violations, kind, counts, callback)
