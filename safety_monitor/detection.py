"""Lazy, serialized YOLO inference with a bounded mailbox per camera."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import threading

import numpy as np


MODEL_PATH = Path(__file__).resolve().parent.parent / "best.pt"


@dataclass(frozen=True)
class Analysis:
    key: tuple
    frame: np.ndarray
    status: str
    violations: int = 0
    kind: str = 'no_helmet'


@dataclass(frozen=True)
class Detection:
    frame: np.ndarray
    status: str
    violations: int
    kind: str = 'no_helmet'

    def __iter__(self):
        # Preserve the image/status interface for existing callers.
        yield self.frame
        yield self.status


class HelmetDetector:
    def __init__(self, path: Path = MODEL_PATH) -> None:
        self.path = path
        self._model = None
        self._lock = threading.Lock()

    def analyze(self, frame: np.ndarray) -> Detection:
        # Loading and prediction both mutate YOLO state; serialize all access.
        with self._lock:
            if self._model is None:
                if not self.path.is_file():
                    raise FileNotFoundError("best.pt")
                from ultralytics import YOLO
                import torch

                # Leave CPU time for capture, Tk and other camera workers.
                cpu_threads = max(1, min(2, (os.cpu_count() or 2) // 2))
                torch.set_num_threads(cpu_threads)

                self._model = YOLO(str(self.path), task="detect")
                # Ultralytics resets CPU threads while setting up the predictor.
                self._model.add_callback('on_predict_start', lambda _: torch.set_num_threads(cpu_threads))
            result = self._model.predict(frame, conf=0.4, imgsz=640, verbose=False)[0]
            names = {int(k): v for k, v in result.names.items()}
            if set(names.values()) != {"Helmet", "No-Helmet", "Person"}:
                raise ValueError("Unexpected model classes")
            violations = int((result.boxes.cls == next(k for k, v in names.items()
                                                       if v == "No-Helmet")).sum().item())
            # Use the exact analyzed image, so boxes never drift onto newer frames.
            annotated = result.plot()
            status = (f"Без каски: {violations}" if violations else
                      "Без каски: не обнаружено")
            status += f" · YOLO: объектов {len(result.boxes.cls)}"
            return Detection(annotated, status, violations)


_DETECTOR = HelmetDetector()


class AnalysisWorker:
    """Keep at most one pending frame; never call Tk from this thread."""

    def __init__(self, detector: HelmetDetector = _DETECTOR) -> None:
        self._detector = detector
        self._condition = threading.Condition()
        self._pending = None
        self._result: Analysis | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="SafetyMonitorYOLO", daemon=True)
        self._thread.start()

    def submit(self, key: tuple, frame: np.ndarray) -> None:
        with self._condition:
            if not self._closed:
                self._pending = key, frame
                self._condition.notify()

    def snapshot(self) -> Analysis | None:
        with self._condition:
            return self._result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = self._result = None
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                key, frame = self._pending
                self._pending = None
            violations = 0
            kind = 'no_helmet'
            try:
                detection = self._detector.analyze(frame)
                annotated, status = detection
                violations = getattr(detection, 'violations', 0)
                kind = getattr(detection, 'kind', 'no_helmet')
            except ModuleNotFoundError:
                annotated, status = frame, "Анализ недоступен: установите зависимости из requirements.txt"
            except FileNotFoundError:
                annotated, status = frame, "Анализ недоступен: файл best.pt не найден"
            except Exception:
                annotated, status = frame, "Ошибка анализа best.pt: проверьте модель и зависимости"
            with self._condition:
                if self._closed:
                    return
                self._result = Analysis(key, annotated, status, violations, kind)
