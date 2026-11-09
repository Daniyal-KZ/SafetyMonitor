"""Real PostgreSQL + desktop scenario for worker rosters and inspector access."""

import gc
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_UI') == '1', 'Desktop access test is opt-in')
class AccessDesktopTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from dotenv import load_dotenv
        from safety_monitor.app import App
        from safety_monitor.database import Database

        self._gc_enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(self._collect)
        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        self.options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                            port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                            user='postgres', password=os.environ['POSTGRES_PASSWORD'])
        self.schema = 'test_access_ui_' + uuid4().hex
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.root = None
        self.addCleanup(self._cleanup)
        self.db = Database(schema=self.schema, **self.options)
        self.db.initialize()
        self.root = App(database=self.db)
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.login('admin')

    def _collect(self):
        gc.collect()
        if self._gc_enabled:
            gc.enable()

    def _cleanup(self):
        import psycopg
        from psycopg import sql
        if self.root is not None:
            # Several opt-in GUI suites create separate Tcl interpreters in one
            # process; CTk's global timers must not survive the test window.
            for after_id in self.root.tk.call('after', 'info'):
                self.root.tk.call('after', 'cancel', after_id)
            self.root.close()
            self.root.report_callback_exception = None
            self.root = None
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def pump(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if self.errors:
                raise self.errors[0][1]
            if predicate():
                return
            time.sleep(.01)
        self.fail('Access UI did not complete its transition')

    def login(self, username):
        self.root.login_frame.username.insert(0, username)
        self.root.login_frame.password.insert(0, '12345')
        self.root.login_frame.submit()
        self.pump(lambda: self.root.screen is not None)

    def click_member(self, member_list, person_id, column):
        table = member_list.table
        row_id = str(person_id)
        table.see(row_id)
        self.root.update_idletasks()
        self.assertTrue(table.winfo_viewable())
        bounds = table.bbox(row_id, column)
        self.assertTrue(bounds, f'Member {person_id} must have a visible table cell')
        x, y, width, height = bounds
        table.event_generate('<ButtonPress-1>', x=x + width // 2, y=y + height // 2)
        table.event_generate('<ButtonRelease-1>', x=x + width // 2, y=y + height // 2)
        self.root.update()
        self.assertEqual(table.focus(), row_id)
        self.assertEqual(table.selection(), (row_id,))

    def space_member(self, member_list):
        table = member_list.table
        table.focus_force()
        self.pump(lambda: self.root.focus_get() == table)
        table.event_generate('<KeyPress-space>')
        table.event_generate('<KeyRelease-space>')
        self.root.update()

    def assert_members(self, member_list, expected):
        expected = set(expected)
        self.assertEqual({key for key, variable in member_list.variables.items() if variable.get()}, expected)
        for row_id in member_list.table.get_children():
            self.assertEqual(member_list.table.set(row_id, 'selected'),
                             '☑' if int(row_id) in expected else '☐')

    def test_create_roster_preferences_and_live_revocation(self):
        import cv2
        import numpy as np
        from PIL import Image
        from safety_monitor.database import DatabaseWorkspaceStore, DatabaseError

        root, db = self.root, self.db
        root.store.update_settings(auto_connect=False)
        external_admin = db.login('admin', '12345')
        inspector_token = db.login('daniyal', '12345')
        inspector_id = db.user(inspector_token).id
        db.save_preferences(inspector_token, {'auto_connect': False})
        db.logout(inspector_token)

        root.show_section('Инспекторы')
        inspectors = root._sections['Инспекторы']
        self.pump(lambda: not inspectors._busy)
        inspectors.add_inspector()
        dialog = inspectors.dialog
        dialog.display_name.insert(0, 'Второй Инспектор')
        dialog.username.insert(0, 'inspector_two')
        dialog.password.insert(0, '12345')
        dialog.submit()
        self.pump(lambda: not dialog.winfo_exists() and not inspectors._busy)
        other = next(user for user in db.list_inspectors(external_admin) if user.username == 'inspector_two')

        with tempfile.TemporaryDirectory() as folder:
            photo = Path(folder) / 'worker.png'
            Image.new('RGB', (200, 160), '#385a70').save(photo)
            root.show_section('Рабочие')
            workers = root._sections['Рабочие']
            self.pump(lambda: not workers._busy)
            for first, last in [('Иван', 'Иванов'), ('Пётр', 'Петров')]:
                workers.add_worker()
                dialog = workers.dialog
                dialog.first_name.insert(0, first)
                dialog.last_name.insert(0, last)
                with patch('safety_monitor.personnel_ui.filedialog.askopenfilename', return_value=str(photo)):
                    dialog.choose_photo()
                self.pump(lambda: not dialog._photo_loading)
                dialog.submit()
                self.pump(lambda: not dialog.winfo_exists() and not workers._busy)
            worker_ids = sorted(workers._rows)
            self.assertEqual(worker_ids, [1000, 1001])
            self.assertTrue(db.get_worker(external_admin, 1000)['photo'])

            # Mouse/key events require mapped widgets, unlike direct variable writes.
            root.deiconify()
            manager = root.screen.workspace_manager
            manager.create_workspace()
            dialog = manager.dialog
            self.pump(lambda: dialog._members_loaded and dialog.winfo_viewable())
            dialog.name_entry.insert(0, 'Монтаж')
            self.click_member(dialog.inspectors_list, inspector_id, 'name')
            self.click_member(dialog.inspectors_list, other.id, 'login')
            # Moving the highlighted row must preserve the previous checkbox.
            self.assert_members(dialog.inspectors_list, [inspector_id, other.id])
            self.click_member(dialog.workers_list, 1000, 'last')
            self.click_member(dialog.workers_list, 1001, 'selected')
            self.assert_members(dialog.workers_list, [1000, 1001])
            # Space switches only the focused row and must also repaint its mark.
            self.space_member(dialog.workers_list)
            self.assert_members(dialog.workers_list, [1000])
            dialog.submit()
            self.pump(lambda: not dialog.winfo_exists() and not getattr(root.screen, '_busy', False)
                      and len(root.store.workspaces) == 1)
            zone = root.store.workspaces[0]
            self.assertEqual(zone.inspector_ids, sorted([inspector_id, other.id]))
            self.assertEqual(zone.worker_ids, [1000])
            persisted = DatabaseWorkspaceStore(db, external_admin).get_workspace(zone.id)
            self.assertEqual(persisted.inspector_ids, sorted([inspector_id, other.id]))
            self.assertEqual(persisted.worker_ids, [1000])
            # Reopening restores checked and unchecked rows from the saved roster.
            manager.rename_workspace()
            dialog = manager.dialog
            self.pump(lambda: dialog._members_loaded and dialog.winfo_viewable())
            self.assert_members(dialog.inspectors_list, [inspector_id, other.id])
            self.assert_members(dialog.workers_list, [1000])
            self.click_member(dialog.inspectors_list, other.id, 'name')
            self.assert_members(dialog.inspectors_list, [inspector_id])
            self.space_member(dialog.inspectors_list)
            self.assert_members(dialog.inspectors_list, [inspector_id, other.id])
            dialog.destroy()
            root.withdraw()

            hidden = root.store.add_workspace('Закрытая зона', [other.id], [1001])
            video = Path(folder) / 'camera.avi'
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
            self.assertTrue(writer.isOpened())
            for _ in range(12):
                writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
            writer.release()
            camera = root.store.add_camera(zone.id, 'Доступная камера', source=str(video))
            camera.rules.helmet = False
            secret_camera = root.store.add_camera(hidden.id, 'Закрытая камера', source=str(video))
            secret_camera.rules.helmet = False
            root.store.save()
            snapshot = db.get_worker(external_admin, 1000)['photo']
            visible_event = db.record_event(external_admin, camera.id, camera.name, zone.name, 1, snapshot)
            db.record_event(external_admin, secret_camera.id, secret_camera.name, hidden.name, 1, snapshot)

            root.logout()
            self.login('daniyal')
            self.assertEqual([item.id for item in root.store.workspaces], [zone.id])
            root.show_section('Рабочие')
            roster = root._sections['Рабочие']
            self.pump(lambda: not roster._busy)
            self.assertEqual(set(roster._rows), {1000})
            roster.table.selection_set('1000')
            roster.select_worker()
            self.pump(lambda: roster._detail is not None)
            self.assertIsNot(roster._image, roster._placeholder)
            root.open_journal()
            journal = root._sections['Журнал']
            self.pump(lambda: not journal._loading)
            self.assertEqual(set(journal._rows), {visible_event})

            root.screen.application_settings()
            preferences = root.screen._dialogs[-1]
            preferences.theme_menu.set('Тёмная')
            preferences.columns_menu.set('1')
            preferences.submit()
            self.pump(lambda: not getattr(root.screen, '_busy', False))
            self.assertEqual(db.load_config(root.token)[0]['settings']['theme'], 'Dark')
            self.assertEqual(db.load_config(external_admin)[0]['settings']['theme'], 'Light')

            tile = root.screen.tiles[camera.id]
            tile.connect()
            self.pump(lambda: tile.state == 'streaming')
            administrator = DatabaseWorkspaceStore(db, external_admin)
            administrator.update_workspace(zone.id, zone.name, [other.id], [1000])
            root.refresh_access()
            self.pump(lambda: not root.store.workspaces and not roster._busy and not journal._loading)
            self.assertTrue(tile.closed)
            self.assertEqual(root.screen.tiles, {})
            self.assertEqual(roster._rows, {})
            self.assertIsNone(roster._workspace_id)
            self.assertIs(roster._image, roster._placeholder)
            self.assertEqual(journal._rows, {})

            # Recovery with an unchanged empty roster must clear fail-closed UI.
            with patch.object(db, 'load_config', side_effect=DatabaseError('offline')):
                root.refresh_access()
                self.pump(lambda: root._access_suspended)
            root.refresh_access()
            self.pump(lambda: not root._access_suspended)
            self.assertNotIn('подтверждения доступа', root.screen.empty_label.cget('text'))

            root.logout()
            self.login('admin')
            self.assertEqual(root.store.settings['theme'], 'Light')
            root.show_section('Рабочие')
            workers = root._sections['Рабочие']
            workers.set_workspace(None)
            self.pump(lambda: not workers._busy)
            workers.table.selection_set('1000')
            workers.select_worker()
            self.pump(lambda: workers._detail is not None)
            with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=True):
                workers.delete_worker()
            self.pump(lambda: not workers._busy and 1000 not in root.store.get_workspace(zone.id).worker_ids)
            self.assertEqual(set(workers._rows), {1001})

            root.show_section('Инспекторы')
            inspectors = root._sections['Инспекторы']
            self.pump(lambda: not inspectors._busy)
            inspectors.table.selection_set(str(other.id))
            inspectors.select_inspector()
            inspectors.edit_inspector()
            dialog = inspectors.dialog
            dialog.display_name.delete(0, 'end')
            dialog.display_name.insert(0, 'Обновлённый инспектор')
            dialog.username.delete(0, 'end')
            dialog.username.insert(0, 'updated_inspector')
            dialog.password.insert(0, 'changed123')
            dialog.submit()
            self.pump(lambda: not dialog.winfo_exists() and not inspectors._busy)
            updated_token = db.login('updated_inspector', 'changed123')
            self.assertEqual(db.user(updated_token).id, other.id)
            self.assertEqual(inspectors.table.set(str(other.id), 'name'), 'Обновлённый инспектор')
            inspectors.table.selection_set(str(other.id))
            inspectors.select_inspector()
            with patch('safety_monitor.personnel_ui.messagebox.askyesno', return_value=True):
                inspectors.delete_inspector()
            self.pump(lambda: not inspectors._busy and all(
                other.id not in item.inspector_ids for item in root.store.workspaces))
            self.assertNotIn(str(other.id), inspectors.table.get_children())
            self.assertEqual(len(root.store.workspaces), 2)
            self.assertEqual(sum(len(item.cameras) for item in root.store.workspaces), 2)
            self.assertEqual(root.store.get_workspace(hidden.id).worker_ids, [1001])
            self.assertEqual(len(db.list_events(root.token)), 2)
            with self.assertRaises(ValueError):
                db.login('updated_inspector', 'changed123')
            with self.assertRaises(ValueError):
                db.load_config(updated_token)


if __name__ == '__main__':
    unittest.main()
