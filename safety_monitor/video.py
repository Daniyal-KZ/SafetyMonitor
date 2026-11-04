"""OpenCV capture, Tk rendering and bounded submission to the PPE analyzer.

The worker owns VideoCapture. Tk consumes only the latest frame. RTSP timeout
options must be passed to open(), rather than set() after creating a capture.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
import time
from typing import Callable

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image

from .models import Camera
from .detection import AnalysisService, EVENT_TIMEZONE, draw_detections
from datetime import datetime


_VIDEO_BACKGROUND = "#17191C"
_BORDER_COLOR = ("#B8BDC5", "#4B5058")
_CARD_COLOR = ("#FFFFFF", "#292C31")
_MUTED_COLOR = ("#565D68", "#C1C5CC")
_FRAME_INTERVAL_MS = 66
_OPEN_TIMEOUT_MS = 5000
_READ_TIMEOUT_MS = 3000
_RETRY_SECONDS = 5.0
_ERROR_STATE_SECONDS = 1.0
_FFMPEG_OPEN_LOCK = threading.Lock()


@dataclass(frozen=True)
class _FramePacket:
    generation: int
    sequence: int
    frame: np.ndarray | None
    state: str
    status: str
    captured: float
    occurred_at: str


def _is_rtsp(source: int | str | None) -> bool:
    return isinstance(source, str) and source.lower().startswith(("rtsp://", "rtsps://"))


class _CaptureWorker:
    """Open, read and release captures on one thread with a bounded mailbox.

    OpenCV's FFmpeg backend reads the transport option during open(). All
    application capture opens share a lock while the temporary option is set.
    The original environment is restored afterwards and connected streams
    retain their own FFmpeg options. RTSP calls have open/read timeouts.
    Native USB drivers may not support read timeouts; their owning worker
    releases in finally, never concurrently with an unfinished read.
    """

    def __init__(self, reconnect_seconds: float | None = None) -> None:
        self._retry_seconds = _RETRY_SECONDS if reconnect_seconds is None else reconnect_seconds
        self._condition = threading.Condition()
        self._packet_lock = threading.Lock()
        self._stop = threading.Event()
        self._generation = 0
        self._source: int | str | None = None
        self._transport = "tcp"
        self._packet: _FramePacket | None = None
        self._capture: cv2.VideoCapture | None = None
        self._sequence = 0
        self._thread = threading.Thread(
            target=self._run, name="SafetyMonitorCapture", daemon=True
        )
        self._thread.start()

    @property
    def capture(self) -> cv2.VideoCapture | None:
        """Inspection only. All native capture operations belong to the worker."""
        with self._packet_lock:
            return self._capture

    def request(self, source: int | str | None, transport: str = "tcp") -> int:
        if transport not in {"tcp", "udp"}:
            raise ValueError("RTSP transport must be tcp or udp")
        with self._condition:
            self._generation += 1
            self._source, self._transport = source, transport
            generation = self._generation
            # Clear the mailbox while a previous native read may still finish.
            with self._packet_lock:
                self._packet = None
            self._condition.notify_all()
        return generation

    def snapshot(self) -> _FramePacket | None:
        with self._packet_lock:
            return self._packet

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def wait_closed(self, timeout: float = 6.0) -> bool:
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=max(0.0, timeout))
        return not self._thread.is_alive()

    def close(self, wait: bool = True) -> bool:
        self.stop()
        return self.wait_closed() if wait else not self._thread.is_alive()

    def _is_current(self, generation: int) -> bool:
        with self._condition:
            return not self._stop.is_set() and self._generation == generation

    def _wait_current(self, generation: int, seconds: float) -> bool:
        with self._condition:
            self._condition.wait_for(
                lambda: self._stop.is_set() or self._generation != generation,
                timeout=max(0.0, seconds),
            )
            return not self._stop.is_set() and self._generation == generation

    def _publish(
        self, generation: int, frame: np.ndarray | None, state: str, status: str
    ) -> None:
        # Generation check and publication are atomic with request().
        with self._condition:
            if self._stop.is_set() or self._generation != generation:
                return
            with self._packet_lock:
                self._sequence += 1
                self._packet = _FramePacket(generation, self._sequence, frame, state, status,
                                            time.monotonic(), datetime.now(EVENT_TIMEZONE).isoformat(timespec="milliseconds"))

    def _run(self) -> None:
        previous_generation = 0
        while not self._stop.is_set():
            with self._condition:
                self._condition.wait_for(
                    lambda: self._stop.is_set() or self._generation != previous_generation
                )
                if self._stop.is_set():
                    return
                generation = previous_generation = self._generation
                source, transport = self._source, self._transport
            if source is None:
                self._publish(generation, None, "disconnected", "Отключено")
                continue
            self._play_source(generation, source, transport)

    def _play_source(self, generation: int, source: int | str, transport: str) -> None:
        while self._is_current(generation):
            self._publish(generation, None, "connecting", "Подключение…")
            try:
                failure = self._capture_once(generation, source, transport)
            except Exception:
                # Exception messages can contain credentials and addresses.
                failure = "Ошибка видеозахвата. Проверьте источник и доступ к камере."
            if not self._is_current(generation):
                return
            self._publish(generation, None, "error", failure or "Видеопоток недоступен")
            if not _is_rtsp(source):
                return
            error_delay = min(_ERROR_STATE_SECONDS, self._retry_seconds)
            if not self._wait_current(generation, error_delay):
                return
            self._publish(
                generation, None, "reconnecting", "Нет связи. Ожидание повторного подключения…"
            )
            if not self._wait_current(generation, self._retry_seconds - error_delay):
                return

    def _open_capture(
        self, capture: cv2.VideoCapture, generation: int, source: int | str, transport: str
    ) -> bool:
        # Files share the lock too, preventing accidental inheritance of an
        # RTSP stream's temporary transport options during concurrent opens.
        while not _FFMPEG_OPEN_LOCK.acquire(timeout=0.1):
            if not self._is_current(generation):
                return False
        try:
            if not self._is_current(generation):
                return False
            if _is_rtsp(source):
                option_name = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
                previous = os.environ.get(option_name)
                os.environ[option_name] = f"rtsp_transport;{transport}"
                try:
                    return capture.open(
                        source, cv2.CAP_FFMPEG,
                        [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _OPEN_TIMEOUT_MS,
                         cv2.CAP_PROP_READ_TIMEOUT_MSEC, _READ_TIMEOUT_MS],
                    )
                finally:
                    if previous is None:
                        os.environ.pop(option_name, None)
                    else:
                        os.environ[option_name] = previous
            backend = cv2.CAP_DSHOW if isinstance(source, int) and os.name == "nt" else cv2.CAP_ANY
            return capture.open(source, backend)
        finally:
            _FFMPEG_OPEN_LOCK.release()

    def _capture_once(self, generation: int, source: int | str, transport: str) -> str | None:
        capture = cv2.VideoCapture()
        with self._packet_lock:
            self._capture = capture
        try:
            if not self._open_capture(capture, generation, source, transport):
                return "Не удалось подключиться. Проверьте адрес, учётные данные и сеть."
            if not self._is_current(generation):
                return None
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Backend may ignore it.
            is_file = isinstance(source, str) and not _is_rtsp(source)
            fps = capture.get(cv2.CAP_PROP_FPS)
            interval = 1 / fps if is_file and math.isfinite(fps) and 1 <= fps <= 240 else 1 / 30
            deadline = time.monotonic()
            failed_reads = 0
            while self._is_current(generation):
                ok, frame = capture.read()
                if not self._is_current(generation):
                    return None
                if not ok or frame is None or frame.size == 0:
                    if is_file:
                        # Only actual local video is looped at EOF.
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        ok, frame = capture.read()
                        if not self._is_current(generation):
                            return None
                    if not ok or frame is None or frame.size == 0:
                        failed_reads += 1
                        if _is_rtsp(source) or is_file or failed_reads >= 3:
                            return "Видеопоток прерван. Изображение недоступно."
                        if not self._wait_current(generation, 0.1):
                            return None
                        continue
                failed_reads = 0
                self._publish(generation, frame, "streaming", "Подключено")
                if is_file or isinstance(source, int):
                    deadline += interval
                    delay = deadline - time.monotonic()
                    if delay > 0:
                        if not self._wait_current(generation, min(delay, 0.25)):
                            return None
                    else:
                        deadline = time.monotonic()
                elif not self._wait_current(generation, 0.001):
                    return None
            return None
        finally:
            try:
                capture.release()
            finally:
                with self._packet_lock:
                    self._capture = None


class VideoPlayerFrame(ctk.CTkFrame):
    """A square camera tile that renders only real source frames."""

    def __init__(
        self, master, on_status: Callable[[str, str], None] | None = None,
        reconnect_seconds: float = 5.0, analysis: AnalysisService | None = None, **kwargs
    ) -> None:
        kwargs.setdefault("fg_color", _CARD_COLOR)
        kwargs.setdefault("corner_radius", 0)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", _BORDER_COLOR)
        super().__init__(master, **kwargs)
        self.camera: Camera | None = None
        self.workspace_name = ""
        self.state = "disconnected"
        self.on_status = on_status
        self.analysis = analysis
        self._last_analysis = 0.0
        self._worker = _CaptureWorker(reconnect_seconds=reconnect_seconds)
        self._generation = 0
        self._closed = False
        self._after_id: str | None = None
        self._last_packet_sequence = -1
        self._cached_frame: np.ndarray | None = None
        self._last_render_key: tuple | None = None
        self._last_status = "Отключено"
        self._placeholder = ctk.CTkImage(
            light_image=Image.new("RGB", (1, 1), _VIDEO_BACKGROUND), size=(1, 1)
        )
        self._image: ctk.CTkImage = self._placeholder

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        header = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0, height=30)
        header.grid(row=0, column=0, padx=10, pady=(4, 2), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        self.name_label = ctk.CTkLabel(
            header, text="Камера", anchor="w", height=24,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
        )
        self.name_label.grid(row=0, column=0, sticky="ew")
        self.status_label = ctk.CTkLabel(
            header, text="● Отключено", text_color=_MUTED_COLOR, height=24,
            font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.status_label.grid(row=0, column=1, padx=(8, 0), sticky="e")

        self.video_area = ctk.CTkFrame(self, fg_color=_VIDEO_BACKGROUND, corner_radius=0)
        self.video_area.grid(row=1, column=0, padx=1, sticky="nsew")
        self.video_area.grid_columnconfigure(0, weight=1)
        self.video_area.grid_rowconfigure(0, weight=1)
        self.video_label = ctk.CTkLabel(
            self.video_area, text="Нет подключения", image=self._placeholder,
            text_color="#BBC0C8", fg_color=_VIDEO_BACKGROUND, width=1, height=1,
            compound="center", font=ctk.CTkFont(family="Segoe UI", size=13),
        )
        self.video_label.grid(row=0, column=0)
        self.source_label = ctk.CTkLabel(
            self, text="Отключено", anchor="w", height=24, text_color=_MUTED_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.source_label.grid(row=2, column=0, padx=10, pady=3, sticky="ew")
        self.analysis_label = ctk.CTkLabel(
            self, text="Детекция: ожидание видео", anchor="w", height=22,
            text_color=_MUTED_COLOR, font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.analysis_label.grid(row=3, column=0, padx=10, pady=(0, 3), sticky="ew")
        self._analysis_caption = "Детекция: ожидание видео"
        self._schedule_tick()

    @property
    def cap(self) -> cv2.VideoCapture | None:
        return self._worker.capture

    @property
    def closed(self) -> bool:
        return self._closed

    def set_camera(
        self, camera: Camera, workspace_name: str = "", auto_connect: bool = True
    ) -> None:
        if self._closed:
            return
        self.camera, self.workspace_name = camera, workspace_name
        self.name_label.configure(text=camera.name)
        self.disconnect()
        if auto_connect and getattr(camera, "enabled", True):
            self.connect()

    def connect(self) -> None:
        if self._closed or self.camera is None:
            return
        if not getattr(self.camera, "enabled", True):
            self.disconnect()
            self._set_status("disconnected", "Камера отключена в настройках")
            return
        if self.analysis:
            self.analysis.reset_camera(self.camera.id)
        self._last_analysis = 0.0
        self._generation = self._worker.request(
            self.camera.source, getattr(self.camera, "transport", "tcp")
        )
        self._last_packet_sequence = -1
        self._clear_frame("Подключение…")
        self._set_status("connecting", "Подключение…")

    def disconnect(self) -> None:
        if self._closed:
            return
        if self.analysis and self.camera:
            self.analysis.reset_camera(self.camera.id)
        self._generation = self._worker.request(None)
        self._last_packet_sequence = -1
        self._clear_frame("Нет подключения")
        self._set_status("disconnected", "Отключено")

    def close(self, wait: bool = True) -> None:
        if self._closed:
            if wait:
                self._worker.wait_closed()
            return
        self._closed = True
        if self.analysis and self.camera:
            self.analysis.reset_camera(self.camera.id)
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._worker.close(wait=wait)
        self._cached_frame = None

    def wait_closed(self, timeout: float = 6.0) -> bool:
        return self._worker.wait_closed(timeout)

    def destroy(self) -> None:
        self.close(wait=False)
        super().destroy()

    def _schedule_tick(self) -> None:
        if not self._closed:
            self._after_id = self.after(_FRAME_INTERVAL_MS, self._tick)

    def _tick(self) -> None:
        self._after_id = None
        if self._closed:
            return
        try:
            packet = self._worker.snapshot()
            if (packet is not None and packet.generation == self._generation
                    and packet.sequence != self._last_packet_sequence):
                self._last_packet_sequence = packet.sequence
                self._set_status(packet.state, packet.status)
                if packet.frame is None:
                    if self.analysis and self.camera:
                        self.analysis.reset_camera(self.camera.id)
                    self._clear_frame(
                        "Нет подключения" if packet.state == "disconnected" else
                        "Нет видеосигнала" if packet.state in {"error", "reconnecting"} else
                        "Подключение…"
                    )
                else:
                    self._cached_frame = packet.frame
                    if self.analysis and self.camera and packet.captured-self._last_analysis >= 0.25:
                        self._last_analysis = packet.captured
                        self.analysis.submit(self.camera, self.workspace_name, packet.frame, packet.captured, packet.occurred_at)
            if self._cached_frame is not None:
                self._render_current_frame()
        finally:
            self._schedule_tick()

    def _set_status(self, state: str, detail: str) -> None:
        if state == self.state and detail == self._last_status:
            return
        self.state, self._last_status = state, detail
        captions = {
            "disconnected": "Отключено", "connecting": "Подключение",
            "streaming": "Подключено", "error": "Нет связи", "reconnecting": "Повтор",
        }
        colors = {
            "streaming": ("#307645", "#93CDA4"),
            "error": ("#A93434", "#E59797"),
            "connecting": ("#805D18", "#D4B477"),
            "reconnecting": ("#805D18", "#D4B477"),
        }
        self.status_label.configure(
            text=f"● {captions.get(state, state)}", text_color=colors.get(state, _MUTED_COLOR)
        )
        short_detail = detail if len(detail) <= 76 else detail[:73] + "…"
        self.source_label.configure(text=short_detail)
        if self.on_status is not None:
            self.on_status(state, detail)

    def _clear_frame(self, text: str) -> None:
        self._cached_frame = None
        self._last_render_key = None
        # A permanently retained placeholder avoids dangling native Tk images
        # on CTk versions that cannot clear a label with image=None.
        self.video_label.configure(image=self._placeholder, text=text)
        self._image = self._placeholder
        self._analysis_caption = "Детекция: ожидание видео"
        self.analysis_label.configure(text=self._analysis_caption)

    def _render_current_frame(self) -> None:
        frame = self._cached_frame
        if frame is None:
            return
        result = self.analysis.display_result(self.camera.id) if self.analysis and self.camera else None
        detections = result.detections if result else ()
        if result is not None:
            names = ", ".join(sorted({item.name for item in detections}))
            caption = f"Объектов: {len(detections)}" + (f" · {names}" if names else " · ничего не обнаружено")
            if not any(getattr(self.camera.rules, rule) for rule in ("helmet", "vest", "gloves", "glasses")):
                caption += " · контроль СИЗ выключен"
        else:
            caption = self.analysis.status if self.analysis else "Детекция недоступна"
        if caption != self._analysis_caption:
            self._analysis_caption = caption
            self.analysis_label.configure(text=caption)
        # Render boxes on the exact frame that produced them, not on a newer
        # camera frame where the person may already have moved.
        if result is not None:
            frame = result.frame
        scaling = self._get_widget_scaling()
        available_width = max(1, int(self.video_area.winfo_width() / scaling) - 4)
        available_height = max(1, int(self.video_area.winfo_height() / scaling) - 4)
        if available_width < 10 or available_height < 10:
            return
        frame_height, frame_width = frame.shape[:2]
        factor = min(available_width / frame_width, available_height / frame_height)
        width = max(1, int(frame_width * factor))
        height = max(1, int(frame_height * factor))
        frame_key = result.captured if result else self._last_packet_sequence
        render_key = frame_key, width, height, scaling, tuple(detections)
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key
        if detections:
            frame = draw_detections(frame, detections)
        if frame.ndim == 2:
            pil_image = Image.fromarray(frame).convert("RGB")
        elif frame.shape[2] == 4:
            pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA))
        else:
            pil_image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        physical_size = max(1, round(width * scaling)), max(1, round(height * scaling))
        pil_image = pil_image.resize(physical_size, Image.Resampling.BILINEAR)
        image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(width, height))
        self.video_label.configure(image=image, text="")
        self._image = image
