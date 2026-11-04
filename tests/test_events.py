from types import SimpleNamespace
import unittest
import threading
import time
from unittest.mock import Mock

import cv2
import numpy as np

from safety_monitor.detection import Analysis
from safety_monitor.events import EventRecorder


class RecorderTests(unittest.TestCase):
    def test_nonblocking_close_does_not_wait_for_database(self):
        started, release = threading.Event(), threading.Event()
        database = Mock()
        database.recording_token.return_value = 'recorder-token'

        def blocked_write(*args, **kwargs):
            started.set()
            release.wait(3)

        database.record_event.side_effect = blocked_write
        recorder = EventRecorder(database, 'token')
        try:
            recorder.submit(SimpleNamespace(id='1', name='Camera'), 'Factory',
                            Analysis((1,), np.zeros((40, 60, 3), dtype=np.uint8), 'violation', 1))
            self.assertTrue(started.wait(2))
            before = time.perf_counter()
            recorder.close(wait=False)
            self.assertLess(time.perf_counter() - before, .2)
            self.assertTrue(recorder._thread.is_alive())
        finally:
            release.set()
            recorder.close()
        database.record_event.assert_called_once()
        database.release_recording_token.assert_called_once_with('recorder-token')

    def test_only_positive_detections_are_saved_with_cooldown_and_jpeg(self):
        database = Mock()
        database.recording_token.return_value = 'recorder-token'
        recorder = EventRecorder(database, 'token')
        camera = SimpleNamespace(id='1', name='Camera')
        frame = np.zeros((40, 60, 3), dtype=np.uint8)
        try:
            recorder.submit(camera, 'Factory', Analysis((1,), frame, 'ok', 0))
            recorder.submit(camera, 'Factory', Analysis((2,), frame, 'violation', 2))
            recorder.submit(camera, 'Factory', Analysis((3,), frame, 'violation', 2))
        finally:
            recorder.close()
        database.record_event.assert_called_once()
        args = database.record_event.call_args.args
        self.assertEqual(args[:5], ('recorder-token', '1', 'Camera', 'Factory', 2))
        database.release_recording_token.assert_called_once_with('recorder-token')
        decoded = cv2.imdecode(np.frombuffer(args[5], dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(decoded.shape, frame.shape)


if __name__ == '__main__':
    unittest.main()
