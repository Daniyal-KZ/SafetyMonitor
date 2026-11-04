"""Opt-in journal GUI regressions; uses in-memory data and controlled I/O delays."""

from datetime import datetime, timezone
import gc
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class _Gate:
    """Let the test observe a background call and release it without sleeping."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def wait(self):
        self.started.set()
        if not self.release.wait(10):
            raise TimeoutError('Test did not release its background database call')


class _JournalDatabase:
    """The UI gets 101-row pages; CSV exports retain their 100-row batches."""

    def __init__(self, count):
        self.rows = {
            event_id: dict(
                id=event_id,
                occurred_at=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
                workspace_name='Factory', camera_name='Camera', kind='no_helmet',
                object_count=1, reviewed_at=None, reviewer=None, note='',
            )
            for event_id in range(1, count + 1)
        }
        self._lock = threading.Lock()
        self.list_calls = []
        self.fail_once_before = set()
        self.list_gates = {}
        self._used_list_gates = []
        self.snapshot_gates = {}
        self.export_gate = None
        self.delete_gate = None
        self.deleted = []
        self._snapshots = {}

    def user(self, _token):
        return SimpleNamespace(is_admin=True)

    def list_events(self, _token, search='', state='all', before=None, *, limit=100):
        with self._lock:
            self.list_calls.append((search, state, before, limit))
            if before in self.fail_once_before:
                self.fail_once_before.remove(before)
                raise OSError('Temporary journal database failure')
        if limit == 100 and self.export_gate is not None:
            self.export_gate.wait()
        with self._lock:
            rows = [dict(row) for event_id, row in sorted(self.rows.items(), reverse=True)
                    if (before is None or event_id < before)
                    and (not search or search.casefold() in row['workspace_name'].casefold())
                    and (state != 'new' or row['reviewed_at'] is None)
                    and (state != 'reviewed' or row['reviewed_at'] is not None)]
            captured = rows[:limit]
            gate = self.list_gates.pop(before, None) if limit == 101 else None
            if gate is not None:
                self._used_list_gates.append(gate)
        if gate is not None:
            gate.wait()
        return captured

    def snapshot(self, event_id):
        from PIL import Image

        if event_id not in self._snapshots:
            output = BytesIO()
            Image.new('RGB', (24, 16), (event_id % 256, 40, 160)).save(output, format='JPEG')
            self._snapshots[event_id] = output.getvalue()
        return self._snapshots[event_id]

    def event_snapshot(self, _token, event_id):
        gate = self.snapshot_gates.get(event_id)
        if gate is not None:
            gate.wait()
        with self._lock:
            if event_id not in self.rows:
                raise ValueError('Event was deleted')
        return self.snapshot(event_id)

    def delete_events(self, _token, event_ids):
        if self.delete_gate is not None:
            self.delete_gate.wait()
        with self._lock:
            self.deleted.append(list(event_ids))
            for event_id in event_ids:
                self.rows.pop(event_id, None)
        return len(event_ids)

    def update_event_note(self, _token, event_id, note):
        with self._lock:
            self.rows[event_id]['note'] = note[:2000]

    def release_all(self):
        for gate in self.snapshot_gates.values():
            gate.release.set()
        for gate in [*self.list_gates.values(), *self._used_list_gates]:
            gate.release.set()
        if self.export_gate is not None:
            self.export_gate.release.set()
        if self.delete_gate is not None:
            self.delete_gate.release.set()


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_UI') == '1',
                     'Desktop journal regression tests are opt-in')
class JournalInteractionTests(unittest.TestCase):
    def setUp(self):
        import customtkinter as ctk

        # These tests pump update(), not mainloop(). A worker cannot dispatch
        # Tk font/image finalizers to that loop, so collect GUI cycles here.
        self._gc_was_enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(self._collect_gui_cycles)
        self.root = ctk.CTk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.journal = None
        self.database = None
        self.addCleanup(self._cleanup)

    def _collect_gui_cycles(self):
        try:
            gc.collect()
        finally:
            if self._gc_was_enabled:
                gc.enable()

    def _cleanup(self):
        if self.database is not None:
            self.database.release_all()
        try:
            if self.journal is not None:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    self.root.update()
                    if not any(self.journal._jobs.running(channel)
                               for channel in ('list', 'snapshot', 'write', 'export')):
                        break
                    time.sleep(.005)
        finally:
            # CTk schedules application-wide titlebar/DPI timers. Cancel them before
            # destroying a temporary interpreter so the next test gets no stale jobs.
            for after_id in self.root.tk.call('after', 'info'):
                self.root.tk.call('after', 'cancel', after_id)
            self.root.destroy()
            self.root.report_callback_exception = None
            self.journal = self.database = self.root = None
            self.callback_errors.clear()

    def pump(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if self.callback_errors:
                raise self.callback_errors[0][1]
            if predicate():
                return
            time.sleep(.005)
        self.fail('Journal did not complete the expected UI transition')

    def open_journal(self, count):
        from safety_monitor.journal_ui import JournalFrame

        self.database = _JournalDatabase(count)
        self.journal = JournalFrame(self.root, self.database, 'test-token')
        self.journal.pack(fill='both', expand=True)
        self.pump(lambda: not self.journal._loading and bool(self.journal._rows))
        return self.journal

    def select(self, event_id):
        self.journal.table.selection_set(str(event_id))
        self.pump(lambda: self.journal._selected == event_id)

    def test_exactly_one_hundred_records_have_no_empty_next_page(self):
        journal = self.open_journal(100)
        self.assertEqual(len(journal._rows), 100)
        self.assertFalse(journal._has_next)
        self.assertEqual(journal.next_button.cget('state'), 'disabled')
        self.assertEqual(journal.previous_button.cget('state'), 'disabled')
        journal.next_page()
        self.root.update()
        self.assertEqual(journal._pages, [None])
        self.assertEqual(len(self.database.list_calls), 1)
        self.assertEqual(self.database.list_calls[0][-1], 101)

    def test_one_hundred_and_one_records_paginate_in_both_directions(self):
        journal = self.open_journal(101)
        self.assertEqual(len(journal._rows), 100)
        self.assertTrue(journal._has_next)
        self.assertEqual(journal.next_button.cget('state'), 'normal')
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        self.assertEqual(len(journal._pages), 2)
        self.assertFalse(journal._has_next)
        self.assertEqual(journal.previous_button.cget('state'), 'normal')
        self.assertEqual(journal.next_button.cget('state'), 'disabled')
        journal.previous_button.invoke()
        self.pump(lambda: not journal._loading and len(journal._rows) == 100)
        self.assertEqual(journal._pages, [None])
        self.assertEqual(max(journal._rows), 101)

    def test_failed_next_page_preserves_current_rows_and_page_history(self):
        journal = self.open_journal(101)
        original_rows = set(journal._rows)
        original_pages = list(journal._pages)
        self.database.fail_once_before.add(min(original_rows))
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and not journal._jobs.running('list'))
        self.assertEqual(set(journal._rows), original_rows)
        self.assertEqual(journal._pages, original_pages)
        self.assertEqual(journal.next_button.cget('state'), 'normal')
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        self.assertEqual(len(journal._pages), 2)

    def test_deleting_only_record_on_last_page_returns_to_previous_page(self):
        journal = self.open_journal(101)
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        self.select(1)
        with patch('safety_monitor.journal_ui.messagebox.askyesno', return_value=True):
            journal.delete_button.invoke()
        self.pump(lambda: not journal._loading and not journal._jobs.running('write')
                  and journal._pages == [None] and len(journal._rows) == 100)
        self.assertEqual(self.database.deleted, [[1]])
        self.assertNotIn(1, journal._rows)
        self.assertFalse(journal._has_next)

    def test_next_page_works_while_snapshot_is_still_loading(self):
        journal = self.open_journal(101)
        gate = self.database.snapshot_gates[101] = _Gate()
        self.select(101)
        self.pump(gate.started.is_set)
        stale_future = journal._jobs._slots['snapshot'][0]
        self.assertTrue(journal._jobs.running('snapshot'))
        self.assertEqual(journal.next_button.cget('state'), 'normal')
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        self.assertFalse(gate.release.is_set())
        gate.release.set()
        self.pump(lambda: stale_future.done() and not journal._jobs.running('snapshot'))
        self.assertEqual(set(journal._rows), {1})
        self.assertIsNone(journal._snapshot)

    def test_delete_works_while_selected_snapshot_is_still_loading(self):
        journal = self.open_journal(2)
        gate = self.database.snapshot_gates[2] = _Gate()
        self.select(2)
        self.pump(gate.started.is_set)
        stale_future = journal._jobs._slots['snapshot'][0]
        self.assertEqual(journal.delete_button.cget('state'), 'normal')
        with patch('safety_monitor.journal_ui.messagebox.askyesno', return_value=True):
            journal.delete_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        self.assertFalse(gate.release.is_set())
        self.assertEqual(self.database.deleted, [[2]])
        gate.release.set()
        self.pump(lambda: stale_future.done() and not journal._jobs.running('snapshot'))
        self.assertEqual(set(journal._rows), {1})
        self.assertIsNone(journal._snapshot)
        self.assertNotIn('Event was deleted', journal.status.cget('text'))

    def test_quick_selection_ignores_stale_snapshot_completion(self):
        journal = self.open_journal(2)
        gate = self.database.snapshot_gates[2] = _Gate()
        self.select(2)
        self.pump(gate.started.is_set)
        stale_future = journal._jobs._slots['snapshot'][0]
        self.select(1)
        self.assertEqual(journal._selected, 1)
        self.pump(lambda: journal._snapshot == self.database.snapshot(1))
        self.assertFalse(gate.release.is_set())
        gate.release.set()
        self.pump(lambda: stale_future.done() and not journal._jobs.running('snapshot')
                  and journal._snapshot == self.database.snapshot(1))
        self.assertEqual(journal.table.selection(), ('1',))
        self.assertEqual(journal._selected, 1)
        self.assertIn('#1', journal.metadata.cget('text'))

    def test_clearing_selection_disables_actions_and_clears_snapshot(self):
        journal = self.open_journal(2)
        self.select(2)
        self.pump(lambda: journal._snapshot is not None)
        self.assertEqual(journal.delete_button.cget('state'), 'normal')
        self.assertEqual(journal.save_note_button.cget('state'), 'normal')
        journal.table.selection_remove(*journal.table.selection())
        self.pump(lambda: journal._selected is None)
        self.assertIsNone(journal._snapshot)
        for control in (journal.delete_button, journal.save_note_button,
                        journal.review_button, journal.export_button):
            self.assertEqual(control.cget('state'), 'disabled')

    def test_changing_status_filter_from_second_page_resets_cursor(self):
        journal = self.open_journal(101)
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})
        new_filter = journal.state_filter.cget('values')[1]
        journal.state_filter.set(new_filter)
        journal.state_filter.cget('command')(new_filter)
        self.pump(lambda: not journal._loading and journal._filters[1] == 'new')
        self.assertEqual(journal._pages, [None])
        self.assertEqual(len(journal._rows), 100)
        self.assertEqual(max(journal._rows), 101)
        self.assertEqual(self.database.list_calls[-1], ('', 'new', None, 101))

    def test_invalid_snapshot_does_not_disable_record_actions_or_pagination(self):
        journal = self.open_journal(101)
        self.database._snapshots[101] = b'Not a JPEG image'
        self.select(101)
        self.pump(lambda: not journal._jobs.running('snapshot'))
        self.assertIsNone(journal._snapshot)
        self.assertEqual(journal.export_button.cget('state'), 'disabled')
        for control in (journal.delete_button, journal.save_note_button,
                        journal.review_button, journal.next_button):
            self.assertEqual(control.cget('state'), 'normal')
        journal.next_button.invoke()
        self.pump(lambda: not journal._loading and set(journal._rows) == {1})

    def test_destroying_journal_ignores_pending_snapshot_completion(self):
        journal = self.open_journal(2)
        gate = self.database.snapshot_gates[2] = _Gate()
        self.select(2)
        self.pump(gate.started.is_set)
        future = journal._jobs._slots['snapshot'][0]
        with patch.object(journal, 'show_snapshot', wraps=journal.show_snapshot) as displayed:
            journal.destroy()
            gate.release.set()
            self.pump(future.done)
            self.root.update()
            displayed.assert_not_called()
        self.assertFalse(journal._jobs.running('snapshot'))
        self.assertEqual(self.callback_errors, [])

    def test_unsaved_comment_survives_switching_between_records(self):
        journal = self.open_journal(2)
        gate = self.database.snapshot_gates[2] = _Gate()
        self.select(2)
        self.pump(gate.started.is_set)
        journal.note.insert(0, 'Inspector draft before saving')
        self.select(1)
        self.pump(lambda: journal._snapshot == self.database.snapshot(1))
        self.assertEqual(journal.note.get(), '')
        self.select(2)
        self.assertEqual(journal.note.get(), 'Inspector draft before saving')
        gate.release.set()
        self.pump(lambda: journal._snapshot == self.database.snapshot(2))
        self.assertEqual(journal.note.get(), 'Inspector draft before saving')

    def test_saved_comment_survives_older_list_result(self):
        journal = self.open_journal(2)
        self.select(2)
        self.pump(lambda: journal._snapshot is not None)
        journal.note.insert(0, 'Newly saved inspector comment')
        stale_list = self.database.list_gates[None] = _Gate()
        journal.refresh(reset=False)
        self.pump(stale_list.started.is_set)
        self.assertEqual(self.database.rows[2]['note'], '')
        journal.save_note_button.invoke()
        self.pump(lambda: not journal._jobs.running('write')
                  and self.database.rows[2]['note'] == 'Newly saved inspector comment')
        self.assertFalse(stale_list.release.is_set())
        stale_list.release.set()
        self.pump(lambda: not journal._loading and not journal._jobs.running('list'))
        self.assertEqual(self.database.rows[2]['note'], 'Newly saved inspector comment')
        self.assertEqual(journal._rows[2]['note'], 'Newly saved inspector comment')
        self.assertEqual(journal._selected, 2)
        self.assertEqual(journal.note.get(), 'Newly saved inspector comment')

    def test_finishing_delete_preserves_pending_next_page(self):
        journal = self.open_journal(101)
        self.select(101)
        self.pump(lambda: journal._snapshot is not None)
        deleting = self.database.delete_gate = _Gate()
        with patch('safety_monitor.journal_ui.messagebox.askyesno', return_value=True):
            journal.delete_button.invoke()
        self.pump(deleting.started.is_set)
        next_page = self.database.list_gates[2] = _Gate()
        self.assertEqual(journal.next_button.cget('state'), 'normal')
        journal.next_button.invoke()
        self.pump(next_page.started.is_set)
        self.assertTrue(journal._loading)
        deleting.release.set()
        self.pump(lambda: not journal._jobs.running('write'))
        self.assertFalse(next_page.release.is_set())
        next_page.release.set()
        self.pump(lambda: not journal._loading and not journal._jobs.running('list'))
        self.assertEqual(self.database.deleted, [[101]])
        self.assertEqual(journal._pages, [None, 2])
        self.assertEqual(set(journal._rows), {1})
        self.assertEqual(journal.previous_button.cget('state'), 'normal')
        self.assertEqual(self.database.list_calls[-1][2], 2)

    def test_csv_export_does_not_block_list_pagination(self):
        journal = self.open_journal(101)
        gate = self.database.export_gate = _Gate()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'journal.csv'
            try:
                with patch('safety_monitor.journal_ui.filedialog.asksaveasfilename',
                           return_value=str(destination)):
                    journal.export_csv()
                self.pump(gate.started.is_set)
                self.assertTrue(journal._jobs.running('export'))
                self.assertEqual(journal.next_button.cget('state'), 'normal')
                journal.next_button.invoke()
                self.pump(lambda: not journal._loading and set(journal._rows) == {1})
                self.assertFalse(gate.release.is_set())
                gate.release.set()
                self.pump(lambda: not journal._jobs.running('export'))
                self.assertTrue(destination.is_file())
                with destination.open(encoding='utf-8-sig') as handle:
                    self.assertEqual(len(handle.readlines()), 102)
            finally:
                gate.release.set()
                self.pump(lambda: not journal._jobs.running('export'))


if __name__ == '__main__':
    unittest.main()
