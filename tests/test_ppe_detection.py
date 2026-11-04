"""Merge regressions: model formats, PPE confirmation and stale camera frames."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from safety_monitor.detection import AnalysisWorker, Detection, HelmetDetector, PPEDetector
from safety_monitor.inference import (LEGACY_MODEL_PATH, RFDETRDetector, read_labels,
                                      resolve_model_path, supported_rule_choices)
from safety_monitor.ppe import BoxDetection, ViolationTracker, missing_equipment


PERSON = BoxDetection("PERSON", .95, (100, 40, 280, 460))


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Worker did not complete")


class ModelMergeTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((500, 700, 3), dtype=np.uint8)

    def rfdetr(self, names=("PERSON", "NO HELMET", "NO VEST"), *, verified=True):
        model = SimpleNamespace(
            predict=lambda _: SimpleNamespace(
                xyxy=np.array([PERSON.box, (130, 45, 220, 100), (120, 160, 230, 250)][:len(names)]),
                confidence=np.full(len(names), .9), class_id=np.arange(len(names))),
            label=lambda key: names[key], labels_verified=verified,
            supported_rules=("helmet", "vest"), resolution=800, device="cpu")
        detector = PPEDetector("weights/model-v2.pt")
        detector._model = model
        return detector

    def test_legacy_yolo_aliases_class_ids_and_disabled_rules(self):
        detector = HelmetDetector()
        result = SimpleNamespace(names={7: "PERSON", 4: "no_helmet", 10: "hardhat"},
                                 boxes=SimpleNamespace(cls=np.array([7, 4, 4, 10])),
                                 plot=lambda: self.frame.copy())
        detector._model = Mock()
        detector._model.predict.return_value = [result]
        detected = detector.analyze(self.frame)
        self.assertEqual(detected.violation_counts, (("no_helmet", 2),))
        self.assertEqual(detector.analyze(self.frame, ()).violation_counts, ())
        self.assertIsNone(detected.observations)

    def test_rfdetr_keeps_multiple_rules_but_defers_journal_to_confirmation(self):
        detector = self.rfdetr()
        detection = detector.analyze(self.frame, ("helmet", "vest", "gloves"))
        self.assertEqual(detection.violation_counts, ())
        self.assertEqual(detection.violations, 0)
        self.assertEqual(detection.observations[0][1], ("helmet", "vest"))
        self.assertIn("Нет контроля: Перчатки", detection.status)
        vest_only = detector.analyze(self.frame, ("vest",))
        self.assertEqual(vest_only.observations[0][1], ("vest",))
        self.assertEqual(detector.analyze(self.frame, ()).observations[0][1], ())

    def test_unverified_map_cannot_create_absence_evidence(self):
        detection = self.rfdetr(verified=False).analyze(self.frame, ("helmet", "vest"))
        self.assertEqual(detection.required, ())
        self.assertTrue(all(not missing for _, missing in detection.observations))
        self.assertIn("журнал выключен", detection.status)

    def test_rfdetr_drops_nonfinite_and_clips_boxes(self):
        detector = self.rfdetr()
        detector._model.predict = lambda _: SimpleNamespace(
            xyxy=np.array([(float("nan"), 1, 20, 30), (0, 0, 20, 20), (-4, -2, 710, 510),
                           (40, 40, 10, 10)]),
            confidence=np.array([.9, float("nan"), .8, .9]), class_id=np.array([0, 0, 0, 0]))
        detection = detector.analyze(self.frame)
        self.assertEqual(detection.boxes, (BoxDetection("PERSON", .8, (0, 0, 700, 500)),))
        self.assertEqual(detection.observations[0][1], ())

    def test_relative_paths_are_independent_of_launch_directory(self):
        self.assertEqual(resolve_model_path("best.pt"), LEGACY_MODEL_PATH)
        self.assertEqual(supported_rule_choices("best.pt"), ("helmet",))
        self.assertEqual(set(supported_rule_choices("weights/model-v2.pt")),
                         {"helmet", "vest", "gloves", "glasses"})

    def test_label_map_hash_id_and_duplicate_alias_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "weights.labels.json"
            self.assertEqual(read_labels(path, "abc", 6), {})
            for data in ({"sha256": "other", "labels": {}},
                         {"sha256": "abc", "labels": {"6": "VEST"}},
                         {"sha256": "abc", "labels": {"1": ""}},
                         {"sha256": "abc", "labels": {"1": "HELMET", "01": "PERSON"}}, []):
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_labels(path, "abc", 6)
            path.write_text(json.dumps({"sha256": "abc", "labels": {"0": None, "5": "VEST"}}),
                            encoding="utf-8")
            self.assertEqual(read_labels(path, "abc", 6), {0: None, 5: "VEST"})

    def test_missing_labels_do_not_invent_class_names(self):
        detector = RFDETRDetector.__new__(RFDETRDetector)
        detector.labels = {0: None, 5: "no_vest"}
        self.assertIsNone(detector.label(0))
        self.assertEqual(detector.label(5), "NO VEST")
        self.assertEqual(detector.label(9), "CLASS 9")


class PPEGeometryTests(unittest.TestCase):
    def test_direct_labels_without_people_and_conflicting_equipment(self):
        negative = BoxDetection("NO HELMET", .9, (10, 10, 60, 60))
        self.assertEqual(missing_equipment([negative], ("helmet",), (500, 700, 3)),
                         [(negative, ("helmet",))])
        helmet = BoxDetection("HELMET", .9, negative.box)
        self.assertEqual(missing_equipment([negative, helmet], ("helmet",), (500, 700, 3)), [])
        self.assertEqual(missing_equipment([negative], ("vest",), (500, 700, 3)), [])

    def test_gear_belongs_to_a_person_and_small_cropped_people_abstain(self):
        other = BoxDetection("PERSON", .9, (400, 40, 580, 460))
        helmet = BoxDetection("HELMET", .9, (140, 45, 220, 90))
        observations = missing_equipment([PERSON, other, helmet], ("helmet",), (500, 700, 3))
        self.assertEqual([missing for _, missing in observations], [(), ("helmet",)])
        for box in ((0, 0, 250, 400), (10, 10, 80, 90)):
            small = BoxDetection("PERSON", .9, box)
            self.assertEqual(missing_equipment([small], ("helmet",), (500, 700, 3))[0][1], ())

    def test_tracker_disappearance_breaks_consecutive_evidence(self):
        tracker = ViolationTracker(confirm_seconds=1, min_samples=2)
        tracker.update([(PERSON, ("helmet",))], 0)
        tracker.update([], .5)
        self.assertEqual(tracker.update([(PERSON, ("helmet",))], 1), [])


class WorkerMergeTests(unittest.TestCase):
    def make_dormant_worker(self):
        worker = AnalysisWorker(SimpleNamespace(analyze=lambda frame: (frame, "ok")))
        worker.close()
        worker._thread.join(3)
        worker._closed = False
        self.addCleanup(worker.close)
        self.frame = np.zeros((500, 700, 3), dtype=np.uint8)
        self.context = ("generation1",)
        worker._requested_context = self.context
        return worker

    def observation(self, missing=("helmet", "vest")):
        return Detection(self.frame, "RF-DETR", 0, violation_counts=(),
                         observations=((PERSON, missing),), boxes=(PERSON,), required=("helmet", "vest"))

    def test_confirmation_waits_30_seconds_and_acknowledges_each_kind_separately(self):
        worker = self.make_dormant_worker()
        for second in range(30):
            self.assertEqual(worker._confirm(self.observation(), self.frame, self.context, second)[0], ())
        counts, status, callback = worker._confirm(self.observation(), self.frame, self.context, 30)
        self.assertEqual(dict(counts), {"no_helmet": 1, "no_vest": 1})
        # A failed database write leaves the same confirmed event available.
        self.assertEqual(dict(worker._confirm(self.observation(), self.frame, self.context, 31)[0]),
                         dict(counts))
        callback("no_helmet")
        next_counts = worker._confirm(self.observation(), self.frame, self.context, 32)[0]
        self.assertEqual(next_counts, (("no_vest", 1),))
        callback("no_vest")
        self.assertEqual(worker._confirm(self.observation(), self.frame, self.context, 33)[0], ())

    def test_rule_change_resets_confirmation_and_discards_old_acknowledgement(self):
        worker = self.make_dormant_worker()
        for second in range(31):
            _, _, old_callback = worker._confirm(self.observation(), self.frame, self.context, second)
        new_context = ("generation2",)
        worker._requested_context = new_context
        old_callback("no_helmet")
        self.assertEqual(worker._acknowledgements, [])
        for second in range(31, 61):
            self.assertEqual(worker._confirm(self.observation(), self.frame, new_context, second)[0], ())
        self.assertEqual(dict(worker._confirm(self.observation(), self.frame, new_context, 61)[0]),
                         {"no_helmet": 1, "no_vest": 1})

    def test_delayed_acknowledgement_does_not_suppress_a_new_absence_episode(self):
        worker = self.make_dormant_worker()
        missing = self.observation(("helmet",))
        for second in range(31):
            counts, _, old_callback = worker._confirm(missing, self.frame, self.context, second)
        self.assertEqual(counts, (("no_helmet", 1),))
        helmet = BoxDetection("HELMET", .9, (140, 45, 220, 90))
        clear = Detection(self.frame, "RF-DETR", 0, violation_counts=(),
                          observations=((PERSON, ()),), boxes=(PERSON, helmet), required=("helmet",))
        for second in range(31, 37):
            self.assertEqual(worker._confirm(clear, self.frame, self.context, second)[0], ())
        for second in range(37, 67):
            self.assertEqual(worker._confirm(missing, self.frame, self.context, second)[0], ())
        # Saving the first event took until the next episode was about to qualify.
        old_callback("no_helmet")
        counts, _, new_callback = worker._confirm(missing, self.frame, self.context, 67)
        self.assertEqual(counts, (("no_helmet", 1),))
        new_callback("no_helmet")
        self.assertEqual(worker._confirm(missing, self.frame, self.context, 68)[0], ())

    def test_delayed_acknowledgement_cannot_match_reused_ids_after_inference_reset(self):
        worker = self.make_dormant_worker()
        for second in range(31):
            _, _, old_callback = worker._confirm(self.observation(), self.frame, self.context, second)
        worker._tracking_context = None  # An inference outage clears temporal evidence.
        for second in range(31, 61):
            self.assertEqual(worker._confirm(self.observation(), self.frame, self.context, second)[0], ())
        old_callback("no_helmet")
        self.assertEqual(dict(worker._confirm(self.observation(), self.frame, self.context, 61)[0]),
                         {"no_helmet": 1, "no_vest": 1})

    def test_recovery_breaks_confirmation(self):
        worker = self.make_dormant_worker()
        for second in range(29):
            worker._confirm(self.observation(), self.frame, self.context, second)
        self.assertEqual(worker._confirm(self.observation(()), self.frame, self.context, 29)[0], ())
        for second in range(30, 60):
            self.assertEqual(worker._confirm(self.observation(), self.frame, self.context, second)[0], ())
        self.assertTrue(worker._confirm(self.observation(), self.frame, self.context, 60)[0])

    def test_inflight_frame_does_not_publish_after_model_or_rule_change(self):
        first_started, release_first = threading.Event(), threading.Event()
        next_started, release_next = threading.Event(), threading.Event()

        def analyze(frame):
            if int(frame[0]) == 1:
                first_started.set()
                release_first.wait(3)
            else:
                next_started.set()
                release_next.wait(3)
            return frame, "ok"

        worker = AnalysisWorker(SimpleNamespace(analyze=analyze))
        try:
            worker.submit(("old", 1), np.array([1]), model_path="best.pt")
            self.assertTrue(first_started.wait(3))
            worker.submit(("new", 2), np.array([2]), required=("vest",), model_path="weights/model-v2.pt")
            release_first.set()
            self.assertTrue(next_started.wait(3))
            self.assertIsNone(worker.snapshot())
            release_next.set()
            wait_until(lambda: worker.snapshot() is not None)
            self.assertEqual(worker.snapshot().key, ("new", 2))
        finally:
            release_first.set()
            release_next.set()
            worker.close()
            worker._thread.join(3)

    def test_injected_legacy_detector_remains_compatible_with_model_selection(self):
        frame = np.zeros((8, 8, 3), dtype=np.uint8)
        detector = SimpleNamespace(analyze=lambda frame: Detection(frame.copy(), "legacy", 2))
        worker = AnalysisWorker(detector)
        try:
            worker.submit((1,), frame, model_path="best.pt")
            wait_until(lambda: worker.snapshot() is not None)
            self.assertEqual(worker.snapshot().violations, 2)
            self.assertIsNone(worker.snapshot().violation_counts)
        finally:
            worker.close()
            worker._thread.join(3)

    def test_inference_failure_breaks_timer_and_names_optional_dependencies(self):
        def fail(_frame):
            raise ModuleNotFoundError("rfdetr")

        worker = AnalysisWorker(SimpleNamespace(analyze=fail))
        try:
            worker._tracking_context = ("old evidence",)
            worker.submit((1,), np.zeros((8, 8, 3), dtype=np.uint8), model_path="weights/model-v2.pt")
            wait_until(lambda: worker.snapshot() is not None)
            self.assertIsNone(worker._tracking_context)
            self.assertEqual(worker.snapshot().violation_counts, ())
            self.assertIn("requirements-rfdetr.txt", worker.snapshot().status)
        finally:
            worker.close()
            worker._thread.join(3)


if __name__ == "__main__":
    unittest.main()
