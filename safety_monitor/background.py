"""Independent, bounded background jobs with latest-result delivery on Tk's thread."""

from concurrent.futures import ThreadPoolExecutor
import sys
import tkinter as tk

from .account_ui import error_text


class ViewJobs:
    def __init__(self, owner):
        self.owner = owner
        self._pools = {}
        self._slots = {}
        self._closed = False
        self._after = None
        tk.Misc.bind(owner, '<Destroy>', self._destroyed, add='+')

    def running(self, channel):
        return channel in self._slots

    def any_running(self):
        return bool(self._slots)

    def submit(self, channel, task, success, failure, *, replace=True):
        if self._closed or (not replace and self.running(channel)):
            return False
        self.cancel(channel)
        if channel not in self._pools:
            self._pools[channel] = ThreadPoolExecutor(max_workers=2 if channel == 'snapshot' else 1,
                                                     thread_name_prefix=f'SafetyMonitor-{channel}')
        future = self._pools[channel].submit(task)
        self._slots[channel] = future, success, failure
        if self._after is None:
            self._after = self.owner.after(20, self._poll)
        return True

    def cancel(self, channel):
        previous = self._slots.pop(channel, None)
        if previous:
            previous[0].cancel()

    def _poll(self):
        self._after = None
        for channel, item in list(self._slots.items()):
            if self._closed:
                return
            future, success, failure = item
            if not future.done() or self._slots.get(channel) is not item:
                continue
            self._slots.pop(channel)
            try:
                result = future.result()
            except Exception as exc:
                callback, value = failure, error_text(exc)
            else:
                callback, value = success, result
            try:
                callback(value)
            except Exception:
                self.owner._root().report_callback_exception(*sys.exc_info())
        if self._slots and not self._closed and self._after is None:
            self._after = self.owner.after(20, self._poll)

    def _destroyed(self, event):
        if event.widget is self.owner:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._after is not None:
            self.owner.after_cancel(self._after)
            self._after = None
        for channel in list(self._slots):
            self.cancel(channel)
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
