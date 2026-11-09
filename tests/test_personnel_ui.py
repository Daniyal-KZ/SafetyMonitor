"""Personnel UI regressions with isolated data and opt-in Tk windows."""

import gc
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from safety_monitor.personnel_ui import MAX_PHOTO_BYTES, photo_preview, read_photo


def make_photo():
    stream = BytesIO()
    Image.new('RGB', (320, 240), '#315b7b').save(stream, format='PNG')
    return stream.getvalue()


class PhotoTests(unittest.TestCase):
    def test_preview_preserves_proportions_and_does_not_change_bytes(self):
        data = make_photo()
        preview = photo_preview(data)
        self.assertEqual(preview.size, (180, 135))
        self.assertEqual(data, make_photo())
        self.assertIsNone(photo_preview(None))

    def test_invalid_and_oversize_images_are_rejected(self):
        for data in (b'not a photo', b'x' * (MAX_PHOTO_BYTES + 1)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                photo_preview(data)

    def test_file_size_is_bounded_before_decode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'photo.jpg'
            path.write_bytes(b'x' * (MAX_PHOTO_BYTES + 100))
            with self.assertRaises(ValueError):
                read_photo(path)


class PersonnelDatabase:
    def __init__(self):
        self.workers = {
            1000: dict(id=1000, first_name='Иван', last_name='Иванов', photo=make_photo()),
            1001: dict(id=1001, first_name='Пётр', last_name='Петров', photo=None),
        }
        self.users = [SimpleNamespace(id=2, username='inspector', display_name='Инспектор', role='inspector')]
        self.calls = []
        self.visible = {1000}
        self.detail_gate = None
        self.detail_started = threading.Event()

    def user(self, token):
        return SimpleNamespace(is_admin=token == 'admin')

    def _called(self, name):
        self.calls.append((name, threading.get_ident()))

    def list_workers(self, token, workspace_id=None, search=''):
        self._called('list_workers')
        allowed = set(self.workers) if token == 'admin' else self.visible
        if workspace_id:
            allowed = allowed & {'zone-a': {1000}, 'zone-b': {1001}}.get(workspace_id, set())
        return [dict(id=w['id'], first_name=w['first_name'], last_name=w['last_name'], has_photo=bool(w['photo']))
                for w in list(self.workers.values()) if w['id'] in allowed
                and search.casefold() in f"{w['id']} {w['first_name']} {w['last_name']}".casefold()]

    def get_worker(self, token, worker_id):
        self._called('get_worker')
        worker = dict(self.workers[worker_id])
        if token != 'admin' and worker_id not in self.visible:
            raise ValueError('Нет доступа к рабочему.')
        if self.detail_gate:
            self.detail_started.set()
            self.detail_gate.wait(3)
        return worker

    def create_worker(self, token, first, last, photo=None):
        self._called('create_worker')
        assert token == 'admin'
        worker_id = max(self.workers, default=999) + 1
        self.workers[worker_id] = dict(id=worker_id, first_name=first, last_name=last, photo=photo)
        return worker_id

    def update_worker(self, token, worker_id, first, last, photo=None):
        self._called('update_worker')
        assert token == 'admin'
        self.workers[worker_id].update(first_name=first, last_name=last, photo=photo)

    def delete_worker(self, token, worker_id):
        self._called('delete_worker')
        assert token == 'admin'
        del self.workers[worker_id]

    def list_inspectors(self, token):
        self._called('list_inspectors')
        return list(self.users)

    def create_inspector(self, token, username, display_name, password):
        self._called('create_inspector')
        assert token == 'admin'
        user = SimpleNamespace(id=3, username=username, display_name=display_name, role='inspector')
        self.users.append(user)
        return user

    def update_inspector(self, token, inspector_id, username, display_name, password=None):
        self._called('update_inspector')
        assert token == 'admin'
        user = SimpleNamespace(id=inspector_id, username=username, display_name=display_name, role='inspector')
        self.users = [user if existing.id == inspector_id else existing for existing in self.users]
        self.last_password = password
        return user

    def delete_inspector(self, token, inspector_id):
        self._called('delete_inspector')
        assert token == 'admin'
        self.users = [user for user in self.users if user.id != inspector_id]


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_UI') == '1', 'Personnel desktop tests are opt-in')
class PersonnelUITests(unittest.TestCase):
    def setUp(self):
        import customtkinter as ctk
        self._gc_was_enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(self._collect)
        self.root = ctk.CTk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.db, self.frame = PersonnelDatabase(), None
        self.changed = Mock()
        self.addCleanup(self._cleanup)

    def _collect(self):
        gc.collect()
        if self._gc_was_enabled:
            gc.enable()

    def _cleanup(self):
        if self.db.detail_gate:
            self.db.detail_gate.set()
        for after_id in self.root.tk.call('after', 'info'):
            self.root.tk.call('after', 'cancel', after_id)
        self.root.destroy()
        self.root.report_callback_exception = None
        self.frame = self.root = None
        self.errors.clear()

    def pump(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if self.errors:
                raise self.errors[0][1]
            if predicate():
                return
            time.sleep(.005)
        self.fail('Personnel UI did not finish the expected operation.')

    def open_workers(self, token='admin'):
        from safety_monitor.personnel_ui import WorkersFrame
        self.frame = WorkersFrame(self.root, self.db, token, on_changed=self.changed)
        self.frame.pack(fill='both', expand=True)
        self.pump(lambda: not self.frame._busy)
        return self.frame

    def select_worker(self, worker_id):
        self.frame.table.selection_set(str(worker_id))
        self.frame.select_worker()
        self.pump(lambda: self.frame._detail is not None)

    def test_worker_create_edit_photo_delete_and_four_digit_id(self):
        frame = self.open_workers()
        self.assertEqual(frame.table.set('1000', 'id'), '1000')
        self.select_worker(1000)
        self.assertIsNot(frame._image, frame._placeholder)
        frame.edit_worker()
        dialog = frame.dialog
        dialog.first_name.delete(0, 'end')
        dialog.first_name.insert(0, 'Новое имя')
        dialog.remove_photo()
        dialog.submit()
        self.pump(lambda: not dialog.winfo_exists() and not frame._busy)
        self.assertEqual(self.db.workers[1000]['first_name'], 'Новое имя')
        self.assertIsNone(self.db.workers[1000]['photo'])
        frame.set_workspace('zone-a', 'Цех')
        self.pump(lambda: not frame._busy)
        self.assertEqual(set(frame._rows), {1000})
        frame.add_worker()
        dialog = frame.dialog
        dialog.first_name.insert(0, 'Анна')
        dialog.last_name.insert(0, 'Смирнова')
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / 'worker.png'
            photo.write_bytes(make_photo())
            with patch('safety_monitor.personnel_ui.filedialog.askopenfilename', return_value=str(photo)):
                dialog.choose_photo()
            self.pump(lambda: not dialog._photo_loading)
            dialog.submit()
            self.pump(lambda: not dialog.winfo_exists() and not frame._busy)
        self.assertIsNone(frame._workspace_id)
        self.assertIn(1002, frame._rows)
        self.assertTrue(self.db.workers[1002]['photo'])
        self.select_worker(1002)
        with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=False):
            frame.delete_worker()
        self.assertIn(1002, self.db.workers)
        with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=True):
            frame.delete_worker()
        self.pump(lambda: not frame._busy)
        self.assertNotIn(1002, self.db.workers)
        self.assertEqual(self.changed.call_count, 3)
        self.assertTrue(all(thread != threading.get_ident() for _, thread in self.db.calls))

    def test_inspector_can_view_filtered_roster_but_cannot_modify_it(self):
        frame = self.open_workers('inspector')
        self.assertEqual(set(frame._rows), {1000})
        self.select_worker(1000)
        self.assertEqual(frame.edit_button.cget('state'), 'disabled')
        frame.add_worker()
        frame.edit_worker()
        frame.delete_worker()
        self.assertIsNone(frame.dialog)
        self.assertFalse(any(name in {'create_worker', 'update_worker', 'delete_worker'} for name, _ in self.db.calls))
        frame.set_workspace('zone-b')
        self.pump(lambda: not frame._busy)
        self.assertEqual(frame._rows, {})
        self.assertIsNone(frame._detail)

    def test_access_invalidation_discards_pending_photo_and_clears_card(self):
        frame = self.open_workers('inspector')
        self.db.detail_gate = threading.Event()
        frame.table.selection_set('1000')
        frame.select_worker()
        self.assertTrue(self.db.detail_started.wait(2))
        self.db.visible.clear()
        frame.invalidate_access()
        self.db.detail_gate.set()
        self.pump(lambda: not frame._busy)
        self.assertEqual(frame._rows, {})
        self.assertIsNone(frame._detail)
        self.assertIs(frame._image, frame._placeholder)

    def test_invalid_photo_shows_error_without_saving_worker(self):
        frame = self.open_workers()
        frame.add_worker()
        dialog = frame.dialog
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.png'
            path.write_bytes(b'invalid image')
            with patch('safety_monitor.personnel_ui.filedialog.askopenfilename', return_value=str(path)):
                dialog.choose_photo()
            self.pump(lambda: not dialog._photo_loading)
        self.assertIn('фото', dialog.error_label.cget('text'))
        self.assertIsNone(dialog._photo)
        self.assertEqual(len(self.db.workers), 2)

    def test_admin_creates_inspector_account_without_worker_card(self):
        from safety_monitor.personnel_ui import InspectorsFrame
        self.frame = InspectorsFrame(self.root, self.db, 'admin', on_changed=self.changed)
        self.frame.pack(fill='both', expand=True)
        self.pump(lambda: not self.frame._busy)
        self.frame.add_inspector()
        dialog = self.frame.dialog
        dialog.display_name.insert(0, 'Второй инспектор')
        dialog.username.insert(0, 'inspector2')
        dialog.password.insert(0, 'secret123')
        dialog.submit()
        self.pump(lambda: not dialog.winfo_exists() and not self.frame._busy)
        self.assertEqual(self.frame.table.set('3', 'username'), 'inspector2')
        self.assertEqual(len(self.db.workers), 2)
        self.changed.assert_called_once()

    def test_admin_edits_inspector_and_deletes_only_after_confirmation(self):
        from safety_monitor.personnel_ui import InspectorsFrame
        self.frame = frame = InspectorsFrame(self.root, self.db, 'admin', on_changed=self.changed)
        frame.pack(fill='both', expand=True)
        self.pump(lambda: not frame._busy)
        self.assertEqual(frame.edit_button.cget('state'), 'disabled')
        frame.table.selection_set('2')
        frame.select_inspector()
        self.assertEqual(frame.delete_button.cget('state'), 'normal')
        frame.edit_button.invoke()
        dialog = frame.dialog
        self.assertEqual(dialog.username.get(), 'inspector')
        self.assertEqual(dialog.password.get(), '')
        dialog.username.delete(0, 'end')
        dialog.username.insert(0, 'inspector_updated')
        dialog.display_name.delete(0, 'end')
        dialog.display_name.insert(0, 'Новое имя')
        dialog.submit()
        self.pump(lambda: not dialog.winfo_exists() and not frame._busy)
        self.assertEqual(frame.table.set('2', 'username'), 'inspector_updated')
        self.assertEqual(frame.table.set('2', 'name'), 'Новое имя')
        self.assertIsNone(self.db.last_password)
        self.assertEqual(frame._selected, 2)
        frame.edit_inspector()
        dialog = frame.dialog
        dialog.password.insert(0, 'new_secret')
        dialog.submit()
        self.pump(lambda: not dialog.winfo_exists() and not frame._busy)
        self.assertEqual(self.db.last_password, 'new_secret')
        with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=False):
            frame.delete_inspector()
        self.assertEqual(len(self.db.users), 1)
        with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=True):
            frame.delete_button.invoke()
        self.pump(lambda: not frame._busy)
        self.assertEqual(frame.table.get_children(), ())
        self.assertEqual(self.db.users, [])
        self.assertEqual(frame.delete_button.cget('state'), 'disabled')
        self.assertEqual(len(self.db.workers), 2)
        self.assertEqual(self.changed.call_count, 3)
        writes = [thread for name, thread in self.db.calls if name in {'update_inspector', 'delete_inspector'}]
        self.assertEqual(len(writes), 3)
        self.assertTrue(all(thread != threading.get_ident() for thread in writes))

    def test_failed_inspector_delete_keeps_row_and_allows_retry(self):
        from safety_monitor.personnel_ui import InspectorsFrame
        self.frame = frame = InspectorsFrame(self.root, self.db, 'admin', on_changed=self.changed)
        frame.pack(fill='both', expand=True)
        self.pump(lambda: not frame._busy)
        frame.table.selection_set('2')
        frame.select_inspector()
        with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=True), \
                patch.object(self.db, 'delete_inspector', side_effect=ValueError('Не удалось удалить.')):
            frame.delete_inspector()
            self.pump(lambda: not frame._busy)
        self.assertEqual(frame.table.get_children(), ('2',))
        self.assertEqual(frame.delete_button.cget('state'), 'normal')
        self.assertIn('Не удалось удалить', frame.status.cget('text'))
        self.changed.assert_not_called()

    def test_inspector_cannot_open_account_mutations(self):
        from safety_monitor.personnel_ui import InspectorsFrame
        self.frame = frame = InspectorsFrame(self.root, self.db, 'inspector')
        frame.pack(fill='both', expand=True)
        frame.add_inspector()
        frame.edit_inspector()
        with patch('safety_monitor.personnel_ui.messagebox.askyesno') as confirmation:
            frame.delete_inspector()
        confirmation.assert_not_called()
        self.assertIsNone(frame.dialog)
        self.assertTrue(all(control.cget('state') == 'disabled'
                            for control in (frame.add_button, frame.edit_button, frame.delete_button)))
        self.assertEqual(self.db.calls, [])


if __name__ == '__main__':
    unittest.main()
