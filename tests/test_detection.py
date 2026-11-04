import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from safety_monitor.detection import AnalysisWorker, HelmetDetector


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Worker did not complete")


class DetectionTests(unittest.TestCase):
    def test_violation_count_uses_class_names(self):
        detector = HelmetDetector()
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        annotated = frame + 1
        result = SimpleNamespace(names={0: "Person", 1: "Helmet", 2: "No-Helmet"},
                                 boxes=SimpleNamespace(cls=np.array([0, 2, 1, 2])),
                                 plot=lambda: annotated)
        detector._model = Mock()
        detector._model.predict.return_value = [result]
        image, status = detector.analyze(frame)
        self.assertIs(image, annotated)
        self.assertEqual(status, "Без каски: 2 · YOLO: объектов 4")
        result.boxes.cls = np.array([])
        self.assertEqual(detector.analyze(frame).status, "Без каски: не обнаружено · YOLO: объектов 0")

    def test_pending_frames_are_replaced(self):
        started, release = threading.Event(), threading.Event()
        calls = []

        def analyze(frame):
            calls.append(int(frame[0]))
            started.set()
            release.wait(3)
            return frame, "ok"

        worker = AnalysisWorker(SimpleNamespace(analyze=analyze))
        try:
            worker.submit((1,), np.array([1]))
            self.assertTrue(started.wait(3))
            worker.submit((2,), np.array([2]))
            worker.submit((3,), np.array([3]))
            release.set()
            wait_until(lambda: worker.snapshot() is not None and worker.snapshot().key == (3,))
            self.assertEqual(calls, [1, 3])
        finally:
            release.set()
            worker.close()
            worker._thread.join(3)
        self.assertFalse(worker._thread.is_alive())
        self.assertIsNone(worker.snapshot())

    def test_missing_weights_preserve_video_with_error(self):
        worker = AnalysisWorker(HelmetDetector(Path("missing-test-weights.pt")))
        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        try:
            worker.submit((1,), frame)
            wait_until(lambda: worker.snapshot() is not None)
            self.assertIs(worker.snapshot().frame, frame)
            self.assertIn("best.pt не найден", worker.snapshot().status)
        finally:
            worker.close()
            worker._thread.join(3)

    def test_close_discards_in_flight_result(self):
        started, release = threading.Event(), threading.Event()

        def analyze(frame):
            started.set()
            release.wait(3)
            return frame, "ok"

        worker = AnalysisWorker(SimpleNamespace(analyze=analyze))
        try:
            worker.submit((1,), np.zeros((8, 8, 3), dtype=np.uint8))
            self.assertTrue(started.wait(3))
            worker.close()
        finally:
            release.set()
            worker.close()
            worker._thread.join(3)
        self.assertIsNone(worker.snapshot())
        self.assertFalse(worker._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
