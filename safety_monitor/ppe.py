"""PPE geometry, tracking and evidence rendering shared by both model backends."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .classes import RULE_CLASSES


@dataclass(frozen=True)
class BoxDetection:
    name: str
    confidence: float
    box: tuple[float, float, float, float]


def iou(a, b) -> float:
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def missing_equipment(detections: list[BoxDetection], required: tuple[str, ...], frame_shape,
                      imgsz: int = 800) -> list[tuple[BoxDetection, tuple[str, ...]]]:
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


def draw_detections(frame, detections: list[BoxDetection]):
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
