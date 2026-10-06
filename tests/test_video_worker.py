"""Headless capture lifecycle tests using a deterministic device backend."""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from safety_monitor.video import _CaptureWorker


class _CaptureFactory:
    def __init__(
        self, *, unavailable=None, file_lengths=None, gated_source=None,
        gated_open=None, read_failures=None, open_errors=None,
    ) -> None:
        self.unavailable = unavailable or set()
        self.file_lengths = file_lengths or {}
        self.gated_source = gated_source
        self.gated_open = gated_open
        self.read_failures = read_failures or {}
        self.open_errors = open_errors or set()
        self.read_started = threading.Event()
        self.open_started = threading.Event()
        self.allow_read = threading.Event()
        self.allow_open = threading.Event()
        self._lock = threading.Lock()
        self._events: list[tuple[str, int | str | None, int]] = []
        self.instances: list[_FakeCapture] = []

    def __call__(self):
        capture = _FakeCapture(self)
        with self._lock:
            self.instances.append(capture)
        return capture

    def record(self, action, source) -> None:
        with self._lock:
            self._events.append((action, source, threading.get_ident()))

    def events(self):
        with self._lock:
            return list(self._events)


class _FakeCapture:
    def __init__(self, factory):
        self.factory = factory
        self.source = None
        self.position = 0
        self.release_count = 0
        self.backend = None
        self.params = None
        self.transport_option = None
        self.transport_after_open = None
        self._gated_once = False

    def open(self, source, backend, params=None):
        self.source, self.backend, self.params = source, backend, params
        self.transport_option = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        self.factory.record("open", source)
        if source == self.factory.gated_open:
            self.factory.open_started.set()
            if not self.factory.allow_open.wait(timeout=2):
                raise RuntimeError("Test did not unblock its backend open")
        self.transport_after_open = os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS")
        if source in self.factory.open_errors:
            raise RuntimeError(f"Open failure with credentials in {source}")
        return source not in self.factory.unavailable

    def get(self, property_id):
        self.factory.record("get", self.source)
        return 60.0

    def set(self, property_id, value):
        if property_id == cv2.CAP_PROP_POS_FRAMES and value == 0:
            self.position = 0
            self.factory.record("rewind", self.source)
        else:
            self.factory.record("set", self.source)
        return True

    def read(self):
        self.factory.record("read", self.source)
        if self.source == self.factory.gated_source and not self._gated_once:
            self._gated_once = True
            self.factory.read_started.set()
            if not self.factory.allow_read.wait(timeout=2):
                raise RuntimeError("Test did not unblock its backend read")
        failure_at = self.factory.read_failures.get(self.source)
        if failure_at is not None and self.position >= failure_at:
            return False, None
        length = self.factory.file_lengths.get(self.source)
        if length is not None and self.position >= length:
            self.factory.record("eof", self.source)
            return False, None
        self.position += 1
        value = self.source if isinstance(self.source, int) else self.position % 256
        frame = np.full((12, 16, 3), value, dtype=np.uint8)
        self.factory.record("frame", self.source)
        return True, frame

    def release(self):
        self.release_count += 1
        self.factory.record("release", self.source)


@contextmanager
def _worker_with_backend(factory):
    with patch("safety_monitor.video.cv2.VideoCapture", side_effect=factory):
        worker = _CaptureWorker()
        try:
            yield worker
        finally:
            factory.allow_read.set()
            factory.allow_open.set()
            worker.close()


class VideoWorkerTests(unittest.TestCase):
    def wait_until(self, predicate, message="Worker did not reach the expected state"):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.003)
        self.fail(message)

    def wait_packet(self, worker, generation, state):
        def matching():
            packet = worker.snapshot()
            return packet if packet and packet.generation == generation and packet.state == state else None
        return self.wait_until(matching)

    def wait_frame(self, worker, generation):
        packet = self.wait_packet(worker, generation, "streaming")
        self.assertIsNotNone(packet.frame)
        return packet

    def test_switch_releases_previous_capture_before_opening_next(self):
        factory = _CaptureFactory()
        caller_thread = threading.get_ident()
        with _worker_with_backend(factory) as worker:
            self.wait_frame(worker, worker.request(0))
            packet = self.wait_frame(worker, worker.request(1))
            self.assertTrue(np.all(packet.frame == 1))
            actions = [(action, source) for action, source, _ in factory.events()]
            self.assertLess(actions.index(("release", 0)), actions.index(("open", 1)))
            device_threads = {thread_id for _, _, thread_id in factory.events()}
            self.assertEqual(len(device_threads), 1)
            self.assertNotIn(caller_thread, device_threads)
        self.assertEqual([cap.release_count for cap in factory.instances], [1, 1])

    def test_failed_open_has_no_frame_or_fallback_and_releases(self):
        factory = _CaptureFactory(unavailable={"missing.mp4"})
        with _worker_with_backend(factory) as worker:
            packet = self.wait_packet(worker, worker.request("missing.mp4"), "error")
            self.assertIsNone(packet.frame)
            self.assertIsNone(worker.capture)
            self.assertEqual(len(factory.instances), 1)
            self.assertEqual(factory.instances[0].release_count, 1)
            self.assertFalse(any(action == "read" for action, _, _ in factory.events()))
            time.sleep(0.03)
            self.assertIsNone(worker.snapshot().frame)

    def test_file_loops_at_eof_using_actual_capture(self):
        factory = _CaptureFactory(file_lengths={"short.mp4": 2})
        with _worker_with_backend(factory) as worker:
            generation = worker.request("short.mp4")
            self.wait_frame(worker, generation)
            self.wait_until(lambda: sum(a == "frame" for a, _, _ in factory.events()) >= 3)
            actions = [action for action, _, _ in factory.events()]
            self.assertIn("eof", actions)
            self.assertLess(actions.index("eof"), actions.index("rewind"))
            self.assertEqual(worker.snapshot().generation, generation)
            self.assertEqual(len(factory.instances), 1)
        self.assertEqual(factory.instances[0].release_count, 1)

    def test_close_is_idempotent_and_waits_for_owned_release(self):
        factory = _CaptureFactory()
        with _worker_with_backend(factory) as worker:
            self.wait_frame(worker, worker.request(0))
            self.assertTrue(worker.close())
            self.assertIsNone(worker.capture)
            self.assertEqual(factory.instances[0].release_count, 1)
            self.assertTrue(worker.close())
            self.assertEqual(factory.instances[0].release_count, 1)

    def test_rapid_switch_discards_stale_native_read(self):
        factory = _CaptureFactory(gated_source=0)
        with _worker_with_backend(factory) as worker:
            worker.request(0)
            self.assertTrue(factory.read_started.wait(timeout=1))
            worker.request(1)
            worker.request(2)
            latest = worker.request(3)
            self.assertIsNone(worker.snapshot())
            factory.allow_read.set()
            packet = self.wait_frame(worker, latest)
            self.assertTrue(np.all(packet.frame == 3))
            actions = [(action, source) for action, source, _ in factory.events()]
            self.assertEqual([source for action, source in actions if action == "open"], [0, 3])
            self.assertLess(actions.index(("release", 0)), actions.index(("open", 3)))

    def test_disconnect_clears_frame_and_does_not_open_another_capture(self):
        factory = _CaptureFactory()
        with _worker_with_backend(factory) as worker:
            self.wait_frame(worker, worker.request(0))
            packet = self.wait_packet(worker, worker.request(None), "disconnected")
            self.assertIsNone(packet.frame)
            self.assertIsNone(worker.capture)
            self.assertEqual(len(factory.instances), 1)
            self.assertEqual(factory.instances[0].release_count, 1)

    def test_idle_worker_opens_nothing(self):
        with patch("safety_monitor.video.cv2.VideoCapture") as constructor:
            worker = _CaptureWorker()
            try:
                packet = self.wait_packet(worker, worker.request(None), "disconnected")
                self.assertIsNone(packet.frame)
                constructor.assert_not_called()
            finally:
                worker.close()

    def test_rtsp_timeout_parameters_and_transport_restore(self):
        source = "rtsp://operator:secret@192.0.2.1:554/live"
        factory = _CaptureFactory()
        with patch.dict(os.environ, {"OPENCV_FFMPEG_CAPTURE_OPTIONS": "existing;value"}):
            with _worker_with_backend(factory) as worker:
                packet = self.wait_frame(worker, worker.request(source, "udp"))
                cap = factory.instances[0]
                self.assertEqual(cap.backend, cv2.CAP_FFMPEG)
                self.assertEqual(cap.params, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                                              cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])
                self.assertEqual(cap.transport_option, "rtsp_transport;udp")
                self.assertEqual(os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"], "existing;value")
                self.assertNotIn("secret", packet.status)
                self.assertNotIn(source, packet.status)

    def test_open_exception_releases_and_restores_absent_env(self):
        source = "rtsp://operator:secret@192.0.2.1/live"
        factory = _CaptureFactory(open_errors={source})
        with patch.dict(os.environ):
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
            with _worker_with_backend(factory) as worker:
                packet = self.wait_packet(worker, worker.request(source), "error")
                self.assertIsNone(packet.frame)
                self.assertNotIn("secret", packet.status)
                self.assertNotIn("OPENCV_FFMPEG_CAPTURE_OPTIONS", os.environ)
                self.assertEqual(factory.instances[0].release_count, 1)
                self.assertIsNone(worker.capture)

    def test_simultaneous_rtsp_opens_keep_individual_transports(self):
        tcp_source, udp_source = "rtsp://192.0.2.1/live", "rtsp://192.0.2.2/live"
        factory = _CaptureFactory(gated_open=tcp_source)
        with patch.dict(os.environ, {"OPENCV_FFMPEG_CAPTURE_OPTIONS": "original;value"}):
            with _worker_with_backend(factory) as tcp_worker:
                with _worker_with_backend(factory) as udp_worker:
                    tcp_generation = tcp_worker.request(tcp_source, "tcp")
                    self.assertTrue(factory.open_started.wait(timeout=1))
                    udp_generation = udp_worker.request(udp_source, "udp")
                    time.sleep(0.03)
                    self.assertEqual(sum(a == "open" for a, _, _ in factory.events()), 1)
                    factory.allow_open.set()
                    self.wait_frame(tcp_worker, tcp_generation)
                    self.wait_frame(udp_worker, udp_generation)
                    for cap in factory.instances:
                        expected = "rtsp_transport;tcp" if cap.source == tcp_source else "rtsp_transport;udp"
                        self.assertEqual(cap.transport_option, expected)
                        self.assertEqual(cap.transport_after_open, expected)
                    self.assertEqual(os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"], "original;value")

    def test_rtsp_read_failure_clears_stale_frame_without_fake_image(self):
        source = "rtsp://192.0.2.1/live"
        factory = _CaptureFactory(read_failures={source: 2})
        with _worker_with_backend(factory) as worker:
            packet = self.wait_packet(worker, worker.request(source), "error")
            self.assertIsNone(packet.frame)
            self.assertIsNone(worker.capture)
            self.assertEqual(factory.instances[0].release_count, 1)
            self.assertEqual(sum(a == "frame" for a, _, _ in factory.events()), 2)

    def test_rtsp_retry_is_real_and_disconnect_interrupts_retry_wait(self):
        source = "rtsp://192.0.2.1/live"
        factory = _CaptureFactory(unavailable={source})
        with patch("safety_monitor.video._RETRY_SECONDS", 0.05):
            with patch("safety_monitor.video._ERROR_STATE_SECONDS", 0.01):
                with _worker_with_backend(factory) as worker:
                    worker.request(source)
                    self.wait_until(lambda: sum(a == "open" for a, _, _ in factory.events()) >= 2)
                    self.wait_packet(worker, worker.request(None), "disconnected")
                    opens = sum(a == "open" for a, _, _ in factory.events())
                    time.sleep(0.08)
                    self.assertEqual(sum(a == "open" for a, _, _ in factory.events()), opens)
                    self.assertTrue(all(cap.release_count == 1 for cap in factory.instances))

    def test_nonblocking_close_never_releases_during_native_read(self):
        factory = _CaptureFactory(gated_source=0)
        with _worker_with_backend(factory) as worker:
            worker.request(0)
            self.assertTrue(factory.read_started.wait(timeout=1))
            started = time.monotonic()
            self.assertFalse(worker.close(wait=False))
            self.assertLess(time.monotonic() - started, 0.1)
            self.assertEqual(factory.instances[0].release_count, 0)
            self.assertFalse(worker.wait_closed(timeout=0))
            factory.allow_read.set()
            self.assertTrue(worker.wait_closed(timeout=1))
            self.assertEqual(factory.instances[0].release_count, 1)

    def test_invalid_transport_rejected_before_touching_backend(self):
        factory = _CaptureFactory()
        with _worker_with_backend(factory) as worker:
            with self.assertRaises(ValueError):
                worker.request("rtsp://192.0.2.1/live", "invalid")
            self.assertEqual(factory.instances, [])


if __name__ == "__main__":
    unittest.main()
