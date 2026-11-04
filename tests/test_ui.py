"""Opt-in desktop + PostgreSQL test using a temporary schema and video."""

import gc
import os
from pathlib import Path
import tempfile
import time
import unittest
from uuid import uuid4


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_UI') == '1', 'Desktop integration test is opt-in')
class DesktopTests(unittest.TestCase):
    def setUp(self):
        # update() does not service Tk calls from a worker the way mainloop()
        # does. Finalize unreachable GUI cycles on this test's main thread.
        self._gc_was_enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(self._collect_gui_cycles)

    def _collect_gui_cycles(self):
        # addCleanup runs after the test frame and its widget locals are gone.
        try:
            gc.collect()
        finally:
            if self._gc_was_enabled:
                gc.enable()

    def test_login_monitoring_event_library_roles_and_logout(self):
        import cv2
        import numpy as np
        import psycopg
        from psycopg import sql
        from dotenv import load_dotenv
        from safety_monitor.app import App
        from safety_monitor.database import Database
        from safety_monitor.detection import Detection
        import tkinter as tk
        from unittest.mock import Mock, patch
        import threading

        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                       port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                       user='postgres', password=os.environ['POSTGRES_PASSWORD'])
        schema = 'test_ui_' + uuid4().hex
        with psycopg.connect(**options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        root = None
        try:
            db = Database(schema=schema, **options)
            db.initialize()

            class PositiveDetector:
                def analyze(self, frame):
                    return Detection(frame.copy(), 'Без каски: 2 · YOLO: объектов 2', 2)

            with tempfile.TemporaryDirectory() as directory:
                video = Path(directory) / 'camera.avi'
                writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
                self.assertTrue(writer.isOpened())
                for _ in range(20):
                    writer.write(np.zeros((120, 160, 3), dtype=np.uint8))
                writer.release()
                root = App(database=db)
                root.withdraw()
                callback_errors = []
                root.report_callback_exception = lambda *args: callback_errors.append(args)

                def pump(predicate, timeout=15):
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        root.update()
                        if callback_errors:
                            raise callback_errors[0][1]
                        if predicate():
                            return
                        time.sleep(.02)
                    self.fail('UI timeout')

                root.login_frame.username.insert(0, 'admin')
                root.login_frame.password.insert(0, 'wrong')
                root.login_frame.submit()
                pump(lambda: not root.login_frame._busy)
                self.assertIsNone(root.screen)
                self.assertIn('Неверный', root.login_frame.error.cget('text'))
                root.login_frame.password.delete(0, 'end')
                root.login_frame.password.insert(0, '12345')
                root.login_frame.submit()
                pump(lambda: root.screen is not None)
                self.assertTrue(root.store.can_manage)
                root.store.update_settings(auto_connect=False)
                inspector_id = next(user.id for user in db.list_inspectors(root.token) if user.username == 'daniyal')
                zone = root.store.add_workspace('UI object', inspector_ids=[inspector_id])
                camera = root.store.add_camera(zone.id, 'UI camera', source=str(video))
                root.screen.workspace_manager.select_workspace(zone.id)
                self.assertEqual(root.screen.camera_table.set(camera.id, 'id'), '1000')
                tile = root.screen.tiles[camera.id]
                self.assertEqual(tile.name_label.cget('text'), '1000 · UI camera')
                tile._analysis._detector = PositiveDetector()
                tile.connect()
                pump(lambda: tile._analysis_sequence >= 0)
                pump(lambda: root.recorder._queue.unfinished_tasks == 0)
                events = db.list_events(root.token)
                self.assertEqual(len(events), 1)
                root.screen._select_camera(camera.id)
                root.screen.camera_settings()
                settings_dialog = root.screen._dialogs[-1]
                settings_dialog.module_vars['helmet'].set(False)
                settings_dialog.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertFalse(camera.rules.helmet)
                root.screen.edit_camera()
                camera_dialog = root.screen._dialogs[-1]
                self.assertEqual(camera_dialog.id_entry.get(), '1000')
                self.assertEqual(camera_dialog.id_entry.cget('state'), 'readonly')
                camera_dialog.name_entry.delete(0, 'end')
                camera_dialog.name_entry.insert(0, 'Updated camera')
                camera_dialog.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(camera.name, 'Updated camera')
                root.screen.application_settings()
                app_settings = root.screen._dialogs[-1]
                self.assertEqual(app_settings.model_paths[app_settings.model_menu.get()], 'best.pt')
                missing_model = next(label for label, path in app_settings.model_paths.items()
                                     if path == 'weights/model-v2.pt')
                app_settings.model_menu.set(missing_model)
                with patch('safety_monitor.dialogs.resolve_model_path') as resolve:
                    resolve.return_value.is_file.return_value = False
                    app_settings.submit()
                self.assertIn('не найден', app_settings.error_label.cget('text'))
                self.assertEqual(root.store.settings['model_path'], 'best.pt')
                legacy_model = next(label for label, path in app_settings.model_paths.items() if path == 'best.pt')
                app_settings.model_menu.set(legacy_model)
                app_settings.columns_menu.set('3')
                app_settings.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(root.store.settings['view_columns'], 3)
                root.screen._set_columns('2')
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(root.store.settings['view_columns'], 2)

                # Selection changes all existing tiles only after the DB save.
                # Analysis is disabled on this camera; no test checkpoint is loaded.
                checkpoint = Path(directory) / 'custom-rfdetr.pt'
                checkpoint.write_bytes(b'test selection only')
                checkpoint.with_suffix('.labels.json').write_text('{}')
                root.screen.application_settings()
                app_settings = root.screen._dialogs[-1]
                with patch('safety_monitor.dialogs.filedialog.askopenfilename', return_value=str(checkpoint)):
                    app_settings._choose_model()
                app_settings.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(root.store.settings['model_path'], str(checkpoint))
                self.assertTrue(all(t.model_path == str(checkpoint) for t in root.screen.tiles.values()))
                root.screen.camera_settings()
                modules = root.screen._dialogs[-1]
                modules.module_vars['vest'].set(True)
                modules.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertTrue(camera.rules.vest)
                root.screen.camera_settings()
                modules = root.screen._dialogs[-1]
                modules.module_vars['vest'].set(False)
                modules.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                root.screen.application_settings()
                app_settings = root.screen._dialogs[-1]
                app_settings.model_menu.set(legacy_model)
                app_settings.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertTrue(all(t.model_path == 'best.pt' for t in root.screen.tiles.values()))

                # Paging must not leave actions targeting a camera on a hidden page.
                second = root.store.add_camera(zone.id, 'Page two camera', source=str(video))
                root.screen.PAGE_SIZE = 1
                root.screen._render_wall()
                root.screen._select_camera(camera.id)
                root.screen.change_page(1)
                self.assertIsNone(root.screen.selected_camera_id)
                self.assertEqual(root.screen.properties_button.cget('state'), 'disabled')
                second_tile = root.screen.tiles[second.id]
                with patch.object(second_tile, 'connect') as connect_visible:
                    root.screen.connect_cameras()
                    connect_visible.assert_called_once()
                with patch.object(second_tile, 'disconnect') as disconnect_visible:
                    root.screen.disconnect_cameras()
                    disconnect_visible.assert_called_once()
                root.screen.change_page(-1)

                root.open_journal()
                journal = root._sections['Журнал']
                self.assertIs(journal.winfo_toplevel(), root)
                self.assertEqual(root.screen.tabs.get(), 'Журнал')
                self.assertEqual(journal.review_button.cget('fg_color'), root.screen.add_button.cget('fg_color'))
                pump(lambda: bool(journal._rows))
                journal.table.selection_set(str(events[0]['id']))
                journal.select_event()
                pump(lambda: journal._snapshot is not None)
                journal.tabs.set('Библиотека кадров')
                self.assertIsNotNone(journal._image)
                journal.note.insert(0, 'UI reviewed')
                journal.save_note()
                pump(lambda: journal.status.cget('text') == 'Комментарий сохранён.')
                self.assertIsNone(db.list_events(root.token)[0]['reviewed_at'])
                journal.review()
                pump(lambda: bool(journal._rows) and next(iter(journal._rows.values()))['reviewed_at'] is not None)
                self.assertEqual(journal.table.heading('kind', 'text'), 'Вид нарушения')
                jpg_path = Path(directory) / 'export.jpg'
                # Reload the frame after review refreshes the list.
                journal.table.selection_set(str(events[0]['id']))
                journal.select_event()
                pump(lambda: journal._snapshot is not None)
                with patch('safety_monitor.journal_ui.filedialog.asksaveasfilename', return_value=str(jpg_path)):
                    journal.export()
                pump(lambda: not getattr(journal, '_busy', False))
                self.assertEqual(jpg_path.read_bytes(), db.event_snapshot(root.token, events[0]['id']))
                extra = db.record_event(root.token, 'another-camera', 'Extra camera', 'UI object', 1,
                                        jpg_path.read_bytes(), kind='no_vest')
                root.show_section('Наблюдение')
                root.open_journal()
                pump(lambda: len(journal._rows) == 2)
                self.assertEqual(journal.table.set(str(extra), 'kind'), 'Отсутствие сигнального жилета')
                csv_path = Path(directory) / 'journal.csv'
                with patch('safety_monitor.journal_ui.filedialog.asksaveasfilename', return_value=str(csv_path)):
                    journal.export_csv()
                pump(lambda: not getattr(journal, '_busy', False))
                exported = csv_path.read_text(encoding='utf-8-sig')
                self.assertIn('Вид нарушения', exported)
                self.assertIn('Отсутствие сигнального жилета', exported)
                journal.table.selection_set(str(extra))
                journal.select_event()
                pump(lambda: not getattr(journal, '_busy', False))
                with patch('safety_monitor.journal_ui.messagebox.askyesno', return_value=False):
                    journal.delete_selected()
                self.assertEqual(len(journal._rows), 2)
                with patch('safety_monitor.journal_ui.messagebox.askyesno', return_value=True):
                    journal.delete_selected()
                pump(lambda: len(journal._rows) == 1)
                with self.assertRaises(ValueError):
                    db.event_snapshot(root.token, extra)
                self.assertEqual(set(root.screen.tabs._tab_dict), {'Наблюдение', 'Камеры', 'Журнал', 'Рабочие', 'Инспекторы'})
                self.assertFalse(hasattr(db, 'change_password'))
                self.assertFalse(hasattr(db, 'create_user'))
                self.assertFalse(any(isinstance(w, tk.Toplevel) for w in root.winfo_children()))
                manager = root.screen.workspace_manager
                self.assertEqual(manager.rename_button.cget('state'), 'normal')
                self.assertEqual(manager.delete_button.cget('state'), 'normal')
                manager.rename_button.invoke()
                pump(lambda: manager.dialog._members_loaded)
                manager.dialog.name_entry.delete(0, 'end')
                manager.dialog.name_entry.insert(0, 'Renamed object')
                save_started, allow_save = threading.Event(), threading.Event()
                real_save = db.save_config

                def delayed_save(*args, **kwargs):
                    save_started.set()
                    if not allow_save.wait(3):
                        raise TimeoutError('Tk did not process its scheduled callback')
                    return real_save(*args, **kwargs)

                with patch.object(db, 'save_config', side_effect=delayed_save):
                    root.after(150, allow_save.set)
                    before_save = time.perf_counter()
                    manager.dialog.submit()
                    submit_elapsed = time.perf_counter() - before_save
                    self.assertLess(submit_elapsed, .3)
                    pump(lambda: not getattr(root.screen, '_busy', False))
                    self.assertTrue(save_started.is_set())
                self.assertEqual(db.load_config(root.token)[0]['workspaces'][0]['name'], 'Renamed object')
                self.assertTrue(all(t.workspace_name == 'Renamed object' for t in root.screen.tiles.values()))
                with patch('safety_monitor.dashboard.messagebox.askyesno', return_value=False):
                    manager.delete_button.invoke()
                self.assertEqual(len(db.load_config(root.token)[0]['workspaces']), 1)
                with patch('safety_monitor.dashboard.messagebox.askyesno', return_value=True):
                    manager.delete_button.invoke()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(db.load_config(root.token)[0]['workspaces'], [])
                self.assertEqual(manager.delete_button.cget('state'), 'disabled')
                self.assertEqual(len(db.list_events(root.token)), 1)
                retired = Mock()
                root.screen._retired_tiles.append(retired)
                old_recorder = root.recorder
                before_logout = time.perf_counter()
                with patch.object(old_recorder, 'close', wraps=old_recorder.close) as close_recorder:
                    root.logout()
                    close_recorder.assert_called_once_with(wait=False)
                self.assertLess(time.perf_counter() - before_logout, .5)
                print(f'UI responsiveness: save submit {submit_elapsed:.3f}s; '
                      f'logout {time.perf_counter()-before_logout:.3f}s')
                retired.close.assert_called_once_with(wait=False)
                retired.wait_closed.assert_not_called()
                self.assertTrue(tile.closed)
                self.assertIsNone(root.token)
                root.login_frame.username.insert(0, 'daniyal')
                root.login_frame.password.insert(0, '12345')
                root.login_frame.submit()
                pump(lambda: root.screen is not None)
                self.assertFalse(root.store.can_manage)
                self.assertEqual(root.screen.add_button.cget('state'), 'disabled')
                self.assertEqual(root.screen.workspace_manager.rename_button.cget('state'), 'disabled')
                self.assertEqual(root.screen.workspace_manager.delete_button.cget('state'), 'disabled')
                root.screen.add_camera()
                self.assertEqual(root.screen._dialogs, [])
                root.open_journal()
                inspector_journal = root._sections['Журнал']
                pump(lambda: not inspector_journal._loading)
                self.assertEqual(inspector_journal._rows, {})
                self.assertFalse(inspector_journal.can_delete)
                self.assertEqual(set(root.screen.tabs._tab_dict), {'Наблюдение', 'Камеры', 'Журнал', 'Рабочие'})
                root.screen.application_settings()
                preferences = root.screen._dialogs[-1]
                self.assertFalse(hasattr(preferences, 'model_menu'))
                preferences.theme_menu.set('Тёмная')
                preferences.columns_menu.set('1')
                preferences.submit()
                pump(lambda: not getattr(root.screen, '_busy', False))
                self.assertEqual(root.store.settings['theme'], 'Dark')
                self.assertEqual(root.store.settings['view_columns'], 1)
                self.assertEqual(db.load_config(root.token)[0]['settings']['theme'], 'Dark')
                self.assertFalse(any(isinstance(w, tk.Toplevel) for w in root.winfo_children()))
                root.logout()
                root.close()
                root = None
        finally:
            if root is not None:
                root.close()
                root.report_callback_exception = None
                root = None
            with psycopg.connect(**options) as conn:
                conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    unittest.main()
