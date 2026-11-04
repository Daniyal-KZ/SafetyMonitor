"""Session-local incident timing and approximate cross-camera duplicate suppression."""
from dataclasses import dataclass, field

import cv2
import numpy as np


def appearance(frame, track):
    """Clothing colour distribution, excluding the head; not a person identity model."""
    if track.kind != 'PERSON':
        return None
    x1, y1, x2, y2 = map(int, track.box)
    height, width = frame.shape[:2]
    if min(x1, y1) <= 2 or x2 >= width-2 or y2 >= height-2 or y2-y1 < 100 or x2-x1 < 32:
        return None
    crop = frame[y1+int((y2-y1)*.25):y1+int((y2-y1)*.95),
                 x1+int((x2-x1)*.15):x2-int((x2-x1)*.15)]
    if not crop.size or float(crop.std()) < 12:
        return None
    hsv = cv2.cvtColor(cv2.resize(crop, (40, 80)), cv2.COLOR_BGR2HSV)
    parts = []
    for stripe in np.array_split(hsv, 2):
        histogram = cv2.calcHist([stripe], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
        parts.append(np.sqrt(histogram / max(1, histogram.sum())))
    return np.concatenate(parts) / np.sqrt(2)


@dataclass
class RuleState:
    seconds: float = 0
    last_missing: float | None = None
    samples: int = 0
    logged: bool = False
    clear_since: float | None = None
    last_clear: float | None = None
    # A delayed writer acknowledgement belongs to one absence episode only.
    # Fresh states also get fresh tokens when inference/tracking is reset.
    episode: object = field(default_factory=object, repr=False)


@dataclass
class IncidentPerson:
    id: int
    workspace: str
    last_seen: float
    descriptor: object = None
    bindings: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    cameras: set = field(default_factory=set)


class IncidentCoordinator:
    def __init__(self, confirm_seconds=30.0, max_gap=3.0, retention=300.0):
        self.confirm_seconds = confirm_seconds
        self.max_gap = max_gap
        self.retention = retention
        self.people = {}
        self.next_id = 1

    def begin_frame(self, now):
        self.people = {key: person for key, person in self.people.items()
                       if now-person.last_seen <= self.retention}
        self.claimed = set()

    def observe(self, camera_id, token, workspace, track, missing, clear, frame, now):
        binding = (token, track.id)
        descriptor = appearance(frame, track)
        person = next((p for p in self.people.values()
                       if p.workspace == workspace and p.bindings.get(camera_id, (None,))[0] == binding
                       and p.id not in self.claimed), None)
        if person is None and descriptor is not None:
            candidates = []
            for candidate in self.people.values():
                if candidate.id in self.claimed or candidate.workspace != workspace or candidate.descriptor is None:
                    continue
                # Two simultaneously visible tracks on one camera cannot be the same person.
                previous = candidate.bindings.get(camera_id)
                if previous and now-previous[1] <= self.max_gap:
                    continue
                similarity = float(np.dot(descriptor, candidate.descriptor))
                if similarity >= .94:
                    candidates.append((similarity, candidate.id))
            candidates.sort(reverse=True)
            if candidates and (len(candidates) == 1 or candidates[0][0]-candidates[1][0] >= .06):
                person = self.people[candidates[0][1]]
        if person is None:
            person = IncidentPerson(self.next_id, workspace, now, descriptor)
            self.next_id += 1
            self.people[person.id] = person
        self.claimed.add(person.id)
        person.bindings[camera_id] = (binding, now)
        person.cameras.add(camera_id)
        person.last_seen = max(person.last_seen, now)
        if descriptor is not None and person.descriptor is None:
            person.descriptor = descriptor
        # Bound memory independently of event history in SQLite.
        if len(self.people) > 512:
            oldest = min((p for p in self.people.values() if p.id != person.id), key=lambda p: p.last_seen)
            del self.people[oldest.id]
        confirmed = []
        for rule in set(person.rules) | set(missing) | set(clear):
            state = person.rules.setdefault(rule, RuleState())
            latest = max((t for t in (state.last_missing, state.last_clear) if t is not None), default=-float('inf'))
            if now < latest:
                continue  # Frames from another camera may arrive out of capture-time order.
            if rule in missing:
                if state.last_missing is None or now-state.last_missing > self.max_gap:
                    state.seconds = 0
                    state.samples = 0
                else:
                    state.seconds += now-state.last_missing
                state.last_missing = now
                state.samples += 1
                state.clear_since = state.last_clear = None
                if state.seconds >= self.confirm_seconds and state.samples >= 5 and not state.logged:
                    confirmed.append(rule)
            else:
                # Unknown/occluded equipment cannot advance the violation timer.
                state.seconds = state.samples = 0
                state.last_missing = None
                if rule in clear:
                    if state.last_clear is None or now-state.last_clear > self.max_gap:
                        state.clear_since = now
                    recovered_before = (state.last_clear is not None
                                        and state.last_clear-state.clear_since >= 5)
                    state.last_clear = now
                    if now-state.clear_since >= 5 and not recovered_before:
                        state.logged = False
                        state.episode = object()
                else:
                    state.clear_since = state.last_clear = None
        return person, tuple(sorted(confirmed))

    @staticmethod
    def mark_logged(person, missing):
        for rule in missing:
            person.rules[rule].logged = True
