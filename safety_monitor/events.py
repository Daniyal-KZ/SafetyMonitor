"""Bounded background snapshot recorder: inference never waits for PostgreSQL."""

import logging
import queue
import threading
import time

import cv2


class EventRecorder:
    def __init__(self, database, token):
        self.database, self.token = database, database.recording_token(token)
        self._queue = queue.Queue(maxsize=16)
        self._stop = threading.Event()
        self._last = {}
        self.last_error = ''
        self._thread = threading.Thread(target=self._run, name='SafetyMonitorJournal', daemon=False)
        self._thread.start()

    def submit(self, camera, workspace_name, analysis):
        if self._stop.is_set():
            return
        now = time.monotonic()
        counts = getattr(analysis, 'violation_counts', None)
        if counts is None:
            counts = ((analysis.kind, analysis.violations),)
        for kind, count in counts:
            key = camera.id, kind
            if count <= 0 or now - self._last.get(key, -60) < 30:
                continue
            # Reserve before publication: a fast failed write must be able to
            # remove the cooldown and let the next analysis retry immediately.
            self._last[key] = now
            try:
                self._queue.put_nowait((camera.id, camera.name, workspace_name,
                                       count, analysis.frame, kind,
                                       getattr(analysis, 'on_recorded', None)))
            except queue.Full:
                self._last.pop(key, None)
                self.last_error = 'Очередь журнала заполнена; часть событий не сохранена.'

    def _run(self):
        try:
            self._drain()
        finally:
            self.database.release_recording_token(self.token)

    def _drain(self):
        while not self._stop.is_set() or not self._queue.empty():
            try:
                camera_id, camera_name, workspace, count, frame, kind, on_recorded = self._queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                height, width = frame.shape[:2]
                if width > 1280:
                    frame = cv2.resize(frame, (1280, round(height * 1280 / width)))
                ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                if not ok:
                    raise ValueError('Не удалось сохранить кадр.')
                event_id = self.database.record_event(self.token, camera_id, camera_name, workspace,
                                                      count, encoded.tobytes(), kind=kind)
                self.last_error = ''
                if event_id is not None and on_recorded is not None:
                    try:
                        on_recorded(kind)
                    except Exception:
                        # The row is already durable; an acknowledgement failure
                        # must not turn a successful insert into a storage retry.
                        logging.getLogger(__name__).exception('Не удалось подтвердить запись анализатору.')
            except Exception:
                # Retry the next positive analysis instead of hiding a DB outage for 30s.
                self._last.pop((camera_id, kind), None)
                self.last_error = 'Событие не сохранено. Проверьте подключение к PostgreSQL.'
                logging.getLogger(__name__).warning(self.last_error)
            finally:
                self._queue.task_done()

    def close(self, wait=True):
        self._stop.set()
        if wait:
            self._thread.join(timeout=6)
        if wait and self._thread.is_alive():
            logging.getLogger(__name__).warning('Запись журнала ещё не завершена при выходе.')
