"""One inference worker, bounded camera mailboxes and temporal PPE checks."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta, timezone
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .classes import RULE_CLASSES, RULE_LABELS
from .events import EventStore
from .incidents import IncidentCoordinator
from .inference import RFDETRDetector, MODEL_PATH, ROOT, model_name

EVENT_TIMEZONE = timezone(timedelta(hours=5), "Asia/Qyzylorda")


@dataclass(frozen=True)
class Detection:
    name: str
    confidence: float
    box: tuple[float, float, float, float]


def iou(a, b) -> float:
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def missing_equipment(detections: list[Detection], required: tuple[str, ...], frame_shape,
                      imgsz: int = 800) -> list[tuple[Detection, tuple[str, ...]]]:
    """Use direct NO labels, plus conservative absence checks on visible people."""
    people = [d for d in detections if d.name == "PERSON" and d.confidence >= 0.5]
    present = [set() for _ in people]
    unknown = [set() for _ in people]
    explicit = [set() for _ in people]
    standalone = []
    negative_rules = {"NO " + RULE_CLASSES[rule]: rule for rule in required}
    for item in detections:
        rule = negative_rules.get(item.name)
        if rule is None or item.confidence < 0.5:
            continue
        # Contradictory labels on the same region are not reliable evidence.
        if any(other.name == RULE_CLASSES[rule] and other.confidence >= 0.5
               and iou(item.box, other.box) >= 0.3 for other in detections):
            continue
        cx, cy = (item.box[0]+item.box[2])/2, (item.box[1]+item.box[3])/2
        candidates = [index for index, person in enumerate(people)
                      if person.box[0] <= cx <= person.box[2]
                      and person.box[1] <= cy <= person.box[3]]
        if len(candidates) == 1:
            explicit[candidates[0]].add(rule)
        elif not candidates:
            # A directly detected bare head/hand is evidence even without a full PERSON box.
            standalone.append((item, (rule,)))
    regions = {"HELMET": (0.0, 0.3), "PPE GLASSES": (0.03, 0.32),
               "VEST": (0.15, 0.72), "GLOVES": (0.25, 0.9)}
    for item in detections:
        if item.name not in regions:
            continue
        cx, cy = (item.box[0]+item.box[2])/2, (item.box[1]+item.box[3])/2
        candidates = []
        for index, person in enumerate(people):
            x1, y1, x2, y2 = person.box
            relative_y = (cy-y1) / max(1, y2-y1)
            lo, hi = regions[item.name]
            if x1 <= cx <= x2 and lo <= relative_y <= hi:
                candidates.append(index)
        if len(candidates) == 1:
            present[candidates[0]].add(item.name)
        elif candidates:
            for index in candidates:
                unknown[index].add(item.name)
    height, width = frame_shape[:2]
    scale = min(1.0, imgsz / max(height, width))
    result = []
    for index, person in enumerate(people):
        x1, y1, x2, y2 = person.box
        # Cropped people and significant overlap cannot support absence evidence.
        visible = min(x1, y1) > 2 and x2 < width-2 and y2 < height-2
        crowded = any(index != other and iou(person.box, p.box) > 0.25 for other, p in enumerate(people))
        missing = [rule for rule in required if rule in explicit[index]]
        if visible and not crowded:
            for rule in required:
                minimum_height = {"helmet": 140, "vest": 140, "gloves": 240, "glasses": 300}[rule]
                name = RULE_CLASSES[rule]
                if (rule not in missing and (y2-y1)*scale >= minimum_height and name not in present[index]
                        and name not in unknown[index]):
                    missing.append(rule)
        result.append((person, tuple(missing)))
    return result + standalone


@dataclass
class Track:
    id: int
    box: tuple
    last_seen: float
    kind: str = "PERSON"
    since: dict[str, float] = field(default_factory=dict)
    samples: dict[str, int] = field(default_factory=dict)
    last_logged: dict[str, float] = field(default_factory=dict)


class ViolationTracker:
    def __init__(self, confirm_seconds: float = 30.0, min_samples: int = 5, cooldown: float = float('inf')):
        self.confirm_seconds, self.min_samples, self.cooldown = confirm_seconds, min_samples, cooldown
        self.tracks: dict[int, Track] = {}
        self.next_id = 1
        self.observed = []

    def update(self, observations, now: float) -> list[tuple[Track, tuple[str, ...]]]:
        self.observed = []
        self.tracks = {key: track for key, track in self.tracks.items() if now-track.last_seen <= 5.0}
        available = set(self.tracks)
        results = []
        for person, missing in observations:
            candidates = sorted(((iou(person.box, self.tracks[key].box), key) for key in available
                                 if self.tracks[key].kind == person.name), reverse=True)
            # Avoid handing one person's accumulated absence history to another.
            if candidates and candidates[0][0] >= 0.3 and (len(candidates) == 1 or candidates[0][0]-candidates[1][0] > 0.1):
                key = candidates[0][1]
                track = self.tracks[key]
                available.remove(key)
                if now-track.last_seen > 1.5:
                    track.since.clear()
                    track.samples.clear()
                track.box, track.last_seen = person.box, now
            else:
                track = Track(self.next_id, person.box, now, kind=person.name)
                self.next_id += 1
                self.tracks[track.id] = track
            for rule in tuple(track.since):
                if rule not in missing:
                    track.since.pop(rule)
                    track.samples.pop(rule, None)
            for rule in missing:
                track.since.setdefault(rule, now)
                track.samples[rule] = track.samples.get(rule, 0) + 1
            self.observed.append((track, missing))
            confirmed = tuple(rule for rule in missing if now-track.since[rule] >= self.confirm_seconds
                              and track.samples[rule] >= self.min_samples
                              and (rule not in track.last_logged or now-track.last_logged[rule] >= self.cooldown))
            if confirmed:
                results.append((track, confirmed))
        # A missing person breaks consecutive evidence even if its track survives.
        for key in available:
            self.tracks[key].since.clear()
            self.tracks[key].samples.clear()
        return results

    @staticmethod
    def mark_logged(track: Track, missing: tuple[str, ...], now: float) -> None:
        for rule in missing:
            track.last_logged[rule] = now


def draw_detections(frame, detections: list[Detection]):
    output = frame.copy()
    for item in detections:
        x1, y1, x2, y2 = map(int, item.box)
        color = (225, 170, 70) if item.name == "PERSON" else (80, 200, 80)
        if item.name.startswith("NO "):
            color = (30, 30, 240)
        cv2.rectangle(output, (x1, y1), (x2, y2), color, 2)
        label = f"{item.name} {item.confidence:.2f}"
        (text_width, text_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, .5, 1)
        left = max(0, min(x1, output.shape[1]-text_width-8))
        top = max(0, y1-text_height-baseline-8)
        cv2.rectangle(output, (left, top), (left+text_width+8, top+text_height+baseline+8), (25, 30, 25), -1)
        cv2.putText(output, label, (left+4, top+text_height+4),
                    cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1, cv2.LINE_AA)
    return output


def event_image(frame, detections, box, caption):
    annotated = draw_detections(frame, detections)
    x1, y1, x2, y2 = map(int, box)
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (30, 30, 240), 3)
    image = Image.fromarray(cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
    except OSError:
        font = ImageFont.load_default(size=17)
    # Caption on a separate panel never hides the evidence in the frame.
    lines = []
    draw = ImageDraw.Draw(image)
    for paragraph in caption.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if line and draw.textlength(candidate, font=font) > image.width-20:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    canvas = Image.new("RGB", (image.width, image.height + len(lines)*23+16), "#18202a")
    canvas.paste(image)
    ImageDraw.Draw(canvas).multiline_text((10, image.height+8), "\n".join(lines), font=font, fill="white", spacing=5)
    return cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)


@dataclass(frozen=True)
class FrameJob:
    camera_id: str
    camera_name: str
    workspace: str
    required: tuple[str, ...]
    token: int
    frame: np.ndarray
    captured: float
    occurred_at: str


@dataclass(frozen=True)
class AnalysisResult:
    completed: float
    captured: float
    frame: np.ndarray
    detections: tuple[Detection, ...]


class AnalysisService:
    """All torch/model/tracker access stays on one worker; no Tk calls here."""
    def __init__(self, events: EventStore, model_path=MODEL_PATH):
        self.events = events
        self.model_path = Path(model_path)
        self._condition = threading.Condition()
        self._pending: OrderedDict[str, FrameJob] = OrderedDict()
        self._tokens: dict[str, int] = {}
        self._results: dict[str, AnalysisResult] = {}
        self._trackers: dict[str, tuple] = {}
        self._incidents = IncidentCoordinator()
        self._stop = False
        self._reload = True
        self._model = None
        self._backend = ""
        self._device = "cpu"
        self._status = "Анализ СИЗ: ожидание модели"
        self._model_hash = ""
        self._logger = logging.getLogger(f"SafetyMonitor.analysis.{id(self)}")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        handler = RotatingFileHandler(events.directory / "analysis.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self._logger.addHandler(handler)
        self._thread = threading.Thread(target=self._run, name="SafetyMonitorAnalysis", daemon=True)
        self._thread.start()

    @property
    def status(self) -> str:
        with self._condition:
            return self._status

    def reload(self, model_path=None) -> None:
        with self._condition:
            if model_path is not None:
                self.model_path = Path(model_path)
            for camera_id in self._tokens:
                self._tokens[camera_id] += 1
            self._reload = True
            self._pending.clear()
            self._results.clear()
            self._condition.notify_all()

    def reset_camera(self, camera_id: str) -> None:
        with self._condition:
            self._tokens[camera_id] = self._tokens.get(camera_id, 0)+1
            self._pending.pop(camera_id, None)
            self._results.pop(camera_id, None)

    def submit(self, camera, workspace: str, frame, captured: float, occurred_at: str) -> None:
        required = tuple(rule for rule in RULE_CLASSES if getattr(camera.rules, rule))
        with self._condition:
            if self._stop or self._reload or self._model is None:
                return
            self._pending[camera.id] = FrameJob(camera.id, camera.name, workspace, required,
                                               self._tokens.setdefault(camera.id, 0), frame, captured, occurred_at)
            self._condition.notify_all()

    def result(self, camera_id: str):
        result = self.display_result(camera_id)
        return list(result.detections) if result else []

    def display_result(self, camera_id: str) -> AnalysisResult | None:
        with self._condition:
            result = self._results.get(camera_id)
            return result if result and time.monotonic()-result.completed < 2.0 else None

    def close(self, timeout: float = 3.0) -> None:
        with self._condition:
            self._stop = True
            self._pending.clear()
            self._condition.notify_all()
        self._thread.join(timeout)

    def _set_status(self, message: str) -> None:
        with self._condition:
            self._status = message

    def _load(self) -> None:
        with self._condition:
            path = self.model_path
        name = model_name(path)
        self._model = None
        self._backend = ""
        self._trackers.clear()
        self._incidents = IncidentCoordinator()
        self._set_status("СИЗ: загрузка модели…")
        try:
            model = RFDETRDetector(path)
            self._device = model.device
            self._backend = "rfdetr"
            self._model_hash = model.digest
            self._model = model
            mapping = "" if model.labels_verified else " · нет карты классов, журнал выключен"
            self._ready_status = f"{name} · RF-DETR · {model.device.upper()}{mapping}"
            self._set_status(self._ready_status)
            self._logger.info("Model loaded: %s sha256=%s labels=%s", name, self._model_hash, model.labels)
        except Exception as exc:
            self._set_status(f"{name}: {str(exc)[:180]}")
            self._logger.exception("Model load")

    def _process(self, job: FrameJob) -> None:
        if time.monotonic()-job.captured > 2.0:
            return
        prediction = self._model.predict(job.frame)
        detections = []
        height, width = job.frame.shape[:2]
        for box, confidence, class_id in zip(prediction.xyxy, prediction.confidence,
                                             prediction.class_id, strict=True):
            name = self._model.label(int(class_id))
            if name is None or not np.isfinite(box).all() or not np.isfinite(confidence):
                continue
            x1, y1, x2, y2 = map(float, box)
            clipped = (max(0, x1), max(0, y1), min(width, x2), min(height, y2))
            if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
                detections.append(Detection(name, float(confidence), clipped))
        with self._condition:
            if self._stop or self._reload or job.token != self._tokens.get(job.camera_id, 0):
                return
            self._results[job.camera_id] = AnalysisResult(time.monotonic(), job.captured,
                                                        job.frame, tuple(detections))
            signature = (job.token, job.required)
            previous = self._trackers.get(job.camera_id)
            tracker = previous[1] if previous and previous[0] == signature else ViolationTracker()
            self._trackers[job.camera_id] = (signature, tracker)
        required = tuple(rule for rule in job.required if rule in self._model.supported_rules)
        observations = missing_equipment(detections, required, job.frame.shape,
                                         imgsz=self._model.resolution)
        tracker.update(observations, job.captured)
        self._incidents.begin_frame(job.captured)
        for track, missing in tracker.observed:
            clear = set()
            if track.kind == 'PERSON':
                x1, y1, x2, y2 = track.box
                for item in detections:
                    cx, cy = (item.box[0]+item.box[2])/2, (item.box[1]+item.box[3])/2
                    if item.confidence >= .5 and x1 <= cx <= x2 and y1 <= cy <= y2:
                        clear.update(rule for rule in required if RULE_CLASSES[rule] == item.name)
            person, missing = self._incidents.observe(job.camera_id, job.token, job.workspace, track,
                                                      missing, clear, job.frame, job.captured)
            if not missing:
                continue
            caption = (f"{job.occurred_at} | {job.workspace} / {job.camera_name} | Трек {person.id}\n"
                       "Не обнаружено: " + ", ".join(RULE_LABELS[rule] for rule in missing) + " · Требует проверки")
            frame = event_image(job.frame, detections, track.box, caption)
            # Recheck after encoding; a disconnected/reconfigured camera cannot publish stale evidence.
            with self._condition:
                if self._stop or self._reload or job.token != self._tokens.get(job.camera_id, 0):
                    return
            self.events.add(occurred_at=job.occurred_at, camera_id=job.camera_id, camera_name=job.camera_name,
                            workspace=job.workspace, track_id=person.id, missing=missing, frame=frame,
                            details={"status": "requires_review", "model_sha256": self._model_hash,
                                     "required": job.required, "person_box": track.box,
                                     "evidence_class": track.kind,
                                     "confirm_seconds": self._incidents.confirm_seconds,
                                     "local_track_id": track.id, "cameras": sorted(person.cameras),
                                     "cross_camera_matching": "approximate_clothing" if person.descriptor is not None else "unavailable",
                                     "detections": [{"class": d.name, "confidence": d.confidence, "box": d.box} for d in detections]})
            self._incidents.mark_logged(person, missing)
            self._logger.info("Event camera=%s track=%s missing=%s", job.camera_id, track.id, ",".join(missing))

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._stop or self._reload or self._pending)
                    if self._stop:
                        return
                    reload_model = self._reload
                    self._reload = False
                    job = None if reload_model else self._pending.popitem(last=False)[1]
                if reload_model:
                    self._load()
                    continue
                try:
                    self._process(job)
                    self._set_status(self._ready_status)
                except Exception as exc:
                    self._logger.exception("Analysis/storage error")
                    self._set_status(f"Ошибка анализа: {str(exc)[:160]}")
                # Discard state for detached cameras, while keeping model allocation shared.
                with self._condition:
                    for key in list(self._trackers):
                        if self._trackers[key][0][0] != self._tokens.get(key, 0):
                            self._trackers.pop(key, None)
        finally:
            self._model = None
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)
