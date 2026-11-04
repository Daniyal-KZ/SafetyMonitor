"""PostgreSQL access, personnel and preference regressions in isolated schemas."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
from PIL import Image
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from safety_monitor.database import AccessDenied, Database, DatabaseWorkspaceStore


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_DB') == '1', 'PostgreSQL integration tests are opt-in')
class WorkspaceAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        cls.options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                           port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                           user='postgres', password=os.environ['POSTGRES_PASSWORD'])

    def setUp(self):
        self.schema = 'test_access_' + uuid4().hex
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(schema=self.schema, **self.options)
        self.db.initialize()
        self.admin = self.db.login('admin', '12345')
        self.inspector = self.db.login('daniyal', '12345')
        self.inspector_id = self.db.user(self.inspector).id
        self.store = DatabaseWorkspaceStore(self.db, self.admin)

    def cleanup_schema(self):
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def zone(self, name='Factory', inspectors=None, workers=None):
        return self.store.add_workspace(name, inspector_ids=inspectors or [], worker_ids=workers or [])

    def test_personal_settings_survive_login_without_mutating_other_accounts_or_revision(self):
        inspector = DatabaseWorkspaceStore(self.db, self.inspector)
        original_revision = inspector.revision
        inspector.update_settings(theme='Dark', view_columns=3, auto_connect=False, reconnect_seconds=9)
        self.assertEqual(inspector.revision, original_revision)
        self.store.reload()
        self.assertEqual(self.store.settings['theme'], 'Light')
        self.assertEqual(self.store.revision, original_revision)
        self.store.update_settings(theme='System', model_path='weights/model-v2.pt')
        self.db.logout(self.inspector)
        token = self.db.login('daniyal', '12345')
        reloaded = DatabaseWorkspaceStore(self.db, token)
        self.assertEqual(reloaded.settings['theme'], 'Dark')
        self.assertEqual(reloaded.settings['view_columns'], 3)
        self.assertFalse(reloaded.settings['auto_connect'])
        self.assertEqual(reloaded.settings['reconnect_seconds'], 9)
        self.assertEqual(reloaded.settings['model_path'], 'weights/model-v2.pt')
        with self.assertRaises(AccessDenied):
            reloaded.update_settings(model_path='best.pt')
        with self.assertRaises(AccessDenied):
            self.db.save_preferences(token, {'model_path': 'best.pt'})
        self.assertEqual(reloaded.settings['model_path'], 'weights/model-v2.pt')

    def test_preferences_do_not_make_stale_workspace_state_current(self):
        stale = self.store.fork()
        self.zone('Saved')
        stale.update_settings(theme='Dark')
        with self.assertRaises(ValueError):
            stale.add_workspace('Must not overwrite Saved')
        stale.reload()
        self.assertEqual([zone.name for zone in stale.workspaces], ['Saved'])
        self.assertEqual(stale.settings['theme'], 'Dark')

    def test_minimal_valid_config_and_personal_document_cannot_override_global_model(self):
        revision = self.db.save_config(self.admin, {'version': 1, 'workspaces': []}, self.store.revision)
        self.assertGreater(revision, self.store.revision)
        with self.db.connect() as conn:
            conn.execute('INSERT INTO user_preferences(user_id,document) VALUES (%s,%s) '
                         'ON CONFLICT (user_id) DO UPDATE SET document=EXCLUDED.document',
                         (self.inspector_id, Jsonb({'theme': 'Dark', 'model_path': 'untrusted.pt'})))
        settings = self.db.load_config(self.inspector)[0]['settings']
        self.assertEqual(settings['theme'], 'Dark')
        self.assertEqual(settings['model_path'], 'best.pt')

    def test_two_inspectors_see_only_assigned_zones_workers_and_journal(self):
        other_user = self.db.create_inspector(self.admin, '  SECOND  ', 'Второй инспектор', 'pass123')
        other_token = self.db.login('second', 'pass123')
        first_worker = self.db.create_worker(self.admin, 'Арман', 'Первый')
        other_worker = self.db.create_worker(self.admin, 'Аслан', 'Второй')
        first = self.zone('One', [self.inspector_id], [first_worker])
        second = self.zone('Two', [other_user.id], [other_worker])
        camera = self.store.add_camera(first.id, 'First camera', source=0)
        other_camera = self.store.add_camera(second.id, 'Second camera', source=1)
        event = self.db.record_event(self.inspector, camera.id, 'spoofed', 'spoofed', 1, b'jpeg')
        other_event = self.db.record_event(other_token, other_camera.id, 'also spoofed', 'One', 1, b'other')
        self.assertEqual([zone['id'] for zone in self.db.load_config(self.inspector)[0]['workspaces']], [first.id])
        self.assertEqual([zone['id'] for zone in self.db.load_config(other_token)[0]['workspaces']], [second.id])
        self.assertEqual([worker['id'] for worker in self.db.list_workers(self.inspector)], [first_worker])
        self.assertEqual(self.db.get_worker(other_token, other_worker)['first_name'], 'Аслан')
        self.assertEqual([row['id'] for row in self.db.list_events(self.inspector)], [event])
        self.assertEqual(self.db.list_events(self.inspector)[0]['camera_name'], 'First camera')
        self.assertEqual(self.db.list_events(self.inspector)[0]['workspace_name'], 'One')
        self.assertEqual(len(self.db.list_events(self.admin)), 2)
        for operation in (
            lambda: self.db.event_snapshot(self.inspector, other_event),
            lambda: self.db.review_event(self.inspector, other_event, 'forbidden'),
            lambda: self.db.update_event_note(self.inspector, other_event, 'forbidden'),
            lambda: self.db.get_worker(self.inspector, other_worker),
            lambda: self.db.list_workers(self.inspector, workspace_id=second.id),
            lambda: self.db.record_event(self.inspector, other_camera.id, '', 'One', 1, b'jpeg'),
        ):
            with self.assertRaises(AccessDenied):
                operation()

    def test_revocation_applies_to_live_session_and_draining_recorder(self):
        zone = self.zone(inspectors=[self.inspector_id])
        camera = self.store.add_camera(zone.id, 'Camera', source=0)
        recorder = self.db.recording_token(self.inspector)
        event = self.db.record_event(recorder, camera.id, camera.name, zone.name, 1, b'jpeg')
        self.store.update_workspace(zone.id, zone.name, [], [])
        self.assertEqual(self.db.load_config(self.inspector)[0]['workspaces'], [])
        self.assertEqual(self.db.list_events(self.inspector), [])
        with self.assertRaises(AccessDenied):
            self.db.event_snapshot(self.inspector, event)
        self.db.logout(self.inspector)
        with self.assertRaises(AccessDenied):
            self.db.record_event(recorder, camera.id, camera.name, zone.name, 1, b'jpeg', kind='no_vest')
        self.db.release_recording_token(recorder)

    def test_worker_photo_update_delete_and_stale_assignment_rollback(self):
        output = BytesIO()
        Image.new('RGB', (1000, 800), 'navy').save(output, format='PNG')
        worker_id = self.db.create_worker(self.admin, '  Тимур ', ' Иманов ', output.getvalue())
        self.assertEqual(worker_id, 1000)
        worker = self.db.get_worker(self.admin, worker_id)
        self.assertEqual((worker['first_name'], worker['last_name']), ('Тимур', 'Иманов'))
        self.assertTrue(worker['has_photo'])
        with Image.open(BytesIO(worker['photo'])) as photo:
            self.assertEqual(photo.format, 'JPEG')
            self.assertEqual(photo.size, (640, 512))
        self.db.update_worker(self.admin, worker_id, 'Новое имя', 'Иманов')
        self.assertEqual(self.db.get_worker(self.admin, worker_id)['photo'], worker['photo'])
        self.db.update_worker(self.admin, worker_id, 'Новое имя', 'Иманов', photo=None)
        self.assertFalse(self.db.get_worker(self.admin, worker_id)['has_photo'])
        zone = self.zone(workers=[worker_id])
        stale = self.store.fork()
        self.db.delete_worker(self.admin, worker_id)
        with self.assertRaises(ValueError):
            stale.rename_workspace(zone.id, 'Stale name')
        self.store.reload()
        self.assertEqual(self.store.get_workspace(zone.id).worker_ids, [])
        next_id = self.db.create_worker(self.admin, 'Новый', 'Рабочий')
        self.assertEqual(next_id, worker_id + 1)
        with self.assertRaises(ValueError):
            self.store.update_workspace(zone.id, 'Invalid', [], [worker_id])
        self.assertEqual(self.store.get_workspace(zone.id).name, 'Factory')

    def test_inspector_cannot_manage_people_or_accounts(self):
        worker_id = self.db.create_worker(self.admin, 'Worker', 'One')
        for operation in (
            lambda: self.db.create_worker(self.inspector, 'Worker', 'Two'),
            lambda: self.db.update_worker(self.inspector, worker_id, 'Worker', 'Two'),
            lambda: self.db.delete_worker(self.inspector, worker_id),
            lambda: self.db.create_inspector(self.inspector, 'illegal', 'User', '12345'),
        ):
            with self.assertRaises(AccessDenied):
                operation()
        with self.assertRaises(ValueError):
            self.db.create_worker(self.admin, 'Worker', 'Three', b'not an image')
        with self.assertRaises(ValueError):
            self.db.create_worker(self.admin, ' ', 'Three')
        with self.assertRaises(ValueError):
            self.db.create_inspector(self.admin, 'ADMIN', 'Duplicate', '12345')
        self.assertEqual([user.id for user in self.db.list_inspectors(self.inspector)], [self.inspector_id])

    def test_worker_ids_are_unique_during_concurrent_creation_and_bounded(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids = list(pool.map(lambda number: self.db.create_worker(self.admin, 'Worker', str(number)), range(6)))
        self.assertEqual(sorted(ids), list(range(1000, 1006)))
        with self.db.connect() as conn:
            conn.execute('ALTER SEQUENCE workers_id_seq RESTART WITH 9999')
        self.assertEqual(self.db.create_worker(self.admin, 'Last', 'Worker'), 9999)
        with self.assertRaisesRegex(ValueError, 'закончились'):
            self.db.create_worker(self.admin, 'Overflow', 'Worker')

    def test_migration_preserves_preferences_events_and_never_regrants_by_name(self):
        zone = self.zone('Legacy')
        camera = self.store.add_camera(zone.id, 'Old camera', source=0)
        document, _ = self.db.load_config(self.admin)
        document['settings']['theme'] = 'Dark'
        for workspace in document['workspaces']:
            workspace.pop('inspector_ids')
            workspace.pop('worker_ids')
        with self.db.connect() as conn:
            conn.execute('UPDATE application_config SET document=%s', (Jsonb(document),))
            conn.execute('DELETE FROM user_preferences')
            conn.execute('DELETE FROM schema_version WHERE version=3')
            camera_event = conn.execute('INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot) '
                                        'VALUES (%s,%s,%s,1,%s) RETURNING id',
                                        (camera.id, 'Old camera', 'Renamed in the past', b'camera')).fetchone()['id']
            name_event = conn.execute('INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot) '
                                      'VALUES (%s,%s,%s,1,%s) RETURNING id',
                                      ('removed-camera', 'Removed camera', 'Legacy', b'name')).fetchone()['id']
            orphan_event = conn.execute('INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot) '
                                        'VALUES (%s,%s,%s,1,%s) RETURNING id',
                                        ('unknown-camera', 'Unknown', 'Not yet created', b'orphan')).fetchone()['id']
        migrated = Database(schema=self.schema, **self.options)
        migrated.initialize()
        self.store.reload()
        self.assertEqual(self.store.settings['theme'], 'Dark')
        self.assertEqual(self.db.load_config(self.inspector)[0]['settings']['theme'], 'Dark')
        self.assertEqual(self.db.load_config(self.inspector)[0]['workspaces'], [])
        with self.db.connect() as conn:
            mapping = {row['id']: row['workspace_id'] for row in conn.execute('SELECT id,workspace_id FROM events')}
        self.assertEqual(mapping, {camera_event: zone.id, name_event: zone.id, orphan_event: None})
        self.store.update_workspace(zone.id, 'Legacy', [self.inspector_id], [])
        self.assertEqual({row['id'] for row in self.db.list_events(self.inspector)}, {camera_event, name_event})
        self.zone('Not yet created', inspectors=[self.inspector_id])
        Database(schema=self.schema, **self.options).initialize()
        self.assertEqual({row['id'] for row in self.db.list_events(self.inspector)}, {camera_event, name_event})
        self.assertEqual(self.db.event_snapshot(self.admin, orphan_event), b'orphan')


if __name__ == '__main__':
    unittest.main()
