"""Run real PostgreSQL integration tests with SAFETY_MONITOR_TEST_DB=1.

Every run uses a new schema; application objects and events remain untouched.
"""

import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql

from safety_monitor.database import AccessDenied, Database, DatabaseWorkspaceStore, hash_password, verify_password
from safety_monitor.models import DEFAULT_SETTINGS, WorkspaceStore


class PasswordTests(unittest.TestCase):
    def test_salted_hash_and_verification(self):
        first, second = hash_password('12345'), hash_password('12345')
        self.assertNotEqual(first, second)
        self.assertNotIn('12345', first.split('$')[-1])
        self.assertTrue(verify_password('12345', first))
        self.assertFalse(verify_password('wrong', first))
        self.assertFalse(verify_password('12345', 'bad-hash'))


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_DB') == '1', 'PostgreSQL integration tests are opt-in')
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        cls.options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                           port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                           user='postgres', password=os.environ['POSTGRES_PASSWORD'])

    def setUp(self):
        self.schema = 'test_' + uuid4().hex
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(schema=self.schema, **self.options)
        self.db.initialize()
        self.admin = self.db.login('admin', '12345')
        self.inspector = self.db.login('daniyal', '12345')

    def cleanup_schema(self):
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_login_roles_logout_and_seed_idempotence(self):
        self.assertTrue(self.db.user(self.admin).is_admin)
        self.assertFalse(self.db.user(self.inspector).is_admin)
        for username, password in [('admin', 'bad'), ('missing', '12345'), ("admin' OR true --", '12345')]:
            with self.assertRaises(AccessDenied):
                self.db.login(username, password)
        self.db.initialize()
        with self.db.connect() as conn:
            users = conn.execute('SELECT username FROM users ORDER BY username').fetchall()
        self.assertEqual([row['username'] for row in users], ['admin', 'daniyal'])
        token = self.db.login('daniyal', '12345')
        self.db.logout(token)
        with self.assertRaises(AccessDenied):
            self.db.load_config(token)

    def test_inspector_cannot_mutate_objects(self):
        inspector_store = DatabaseWorkspaceStore(self.db, self.inspector)
        with self.assertRaises(AccessDenied):
            inspector_store.add_workspace('Forbidden')
        self.assertEqual(inspector_store.workspaces, [])
        admin_store = DatabaseWorkspaceStore(self.db, self.admin)
        zone = admin_store.add_workspace('Factory')
        admin_store.add_camera(zone.id, 'Camera', source=0)
        reloaded = DatabaseWorkspaceStore(self.db, self.inspector)
        self.assertEqual(reloaded.workspaces[0].cameras[0].name, 'Camera')

    def test_concurrent_configuration_changes_are_not_overwritten(self):
        first = DatabaseWorkspaceStore(self.db, self.admin)
        second = DatabaseWorkspaceStore(self.db, self.admin)
        first.add_workspace('Saved')
        with self.assertRaises(ValueError):
            second.add_workspace('Stale')
        self.assertEqual(second.workspaces, [])
        self.assertEqual(DatabaseWorkspaceStore(self.db, self.admin).workspaces[0].name, 'Saved')

    def test_model_selection_persists_and_requires_administrator(self):
        administrator = DatabaseWorkspaceStore(self.db, self.admin)
        self.assertEqual(administrator.settings['model_path'], 'best.pt')
        administrator.update_settings(model_path='weights/model-v2.pt')
        inspector = DatabaseWorkspaceStore(self.db, self.inspector)
        self.assertEqual(inspector.settings['model_path'], 'weights/model-v2.pt')
        with self.assertRaises(AccessDenied):
            inspector.update_settings(model_path='best.pt')
        self.assertEqual(inspector.settings['model_path'], 'weights/model-v2.pt')

    def test_events_cooldown_filters_snapshot_review_and_history(self):
        image = b'jpeg-content'
        event = self.db.record_event(self.inspector, 'camera-1', 'Entrance', 'Factory', 2, image)
        self.assertIsNotNone(event)
        self.assertIsNone(self.db.record_event(self.admin, 'camera-1', 'Entrance', 'Factory', 3, image))
        second = self.db.record_event(self.admin, 'camera-2', 'Exit', 'Factory', 1, image)
        rows = self.db.list_events(self.inspector, state='new')
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.db.event_snapshot(self.inspector, event), image)
        self.assertEqual(len(self.db.list_events(self.admin, search='Entrance')), 1)
        self.assertEqual(self.db.list_events(self.admin, before=second)[0]['id'], event)
        self.db.review_event(self.inspector, event, 'Checked')
        reviewed = self.db.list_events(self.admin, state='reviewed')
        self.assertEqual(reviewed[0]['note'], 'Checked')
        self.assertEqual(reviewed[0]['reviewer'], 'Даниял')
        with self.assertRaises(ValueError):
            self.db.review_event(self.admin, event, 'Overwrite')
        # Config deletion does not erase historical events or their images.
        store = DatabaseWorkspaceStore(self.db, self.admin)
        zone = store.add_workspace('Temporary')
        store.remove_workspace(zone.id)
        self.assertEqual(len(self.db.list_events(self.inspector)), 2)

    def test_database_starts_empty_and_preserves_saved_objects(self):
        document, _ = self.db.load_config(self.admin)
        self.assertEqual(document['workspaces'], [])
        self.assertEqual(document['settings'], DEFAULT_SETTINGS)
        store = DatabaseWorkspaceStore(self.db, self.admin)
        store.add_workspace('Saved in PostgreSQL')
        self.db.initialize()
        reloaded = DatabaseWorkspaceStore(self.db, self.inspector)
        self.assertEqual(reloaded.workspaces[0].name, 'Saved in PostgreSQL')

    def test_local_or_in_memory_persistence_is_not_available(self):
        with self.assertRaises(TypeError):
            WorkspaceStore()

    def test_recording_can_drain_after_logout_without_account_permissions(self):
        recording = self.db.recording_token(self.inspector)
        self.db.logout(self.inspector)
        with self.assertRaises(AccessDenied):
            self.db.load_config(recording)
        with self.assertRaises(AccessDenied):
            self.db.load_config(self.inspector)
        self.assertIsNotNone(self.db.record_event(recording, 'draining', 'Camera', 'Factory', 1, b'jpeg'))
        self.db.release_recording_token(recording)
        with self.assertRaises(AccessDenied):
            self.db.record_event(recording, 'after-drain', 'Camera', 'Factory', 1, b'jpeg')

    def test_committed_draft_preserves_live_references(self):
        store = DatabaseWorkspaceStore(self.db, self.admin)
        workspace = store.add_workspace('Factory')
        camera = store.add_camera(workspace.id, 'Before', source=0)
        draft = store.fork()
        draft.update_camera(workspace.id, camera.id, name='After', description='',
                            source=0, transport='tcp', enabled=True)
        self.assertEqual(camera.name, 'Before')
        store.adopt(draft)
        self.assertIs(store.get_workspace(workspace.id), workspace)
        self.assertIs(workspace.cameras[0], camera)
        self.assertEqual(camera.name, 'After')

    def test_journal_kinds_notes_deletion_permissions_and_migration(self):
        original = self.db.record_event(self.admin, 'camera', 'Camera', 'Factory', 1, b'jpeg')
        # Reproduce the original constraint to verify the upgrade path too.
        with self.db.connect() as conn:
            conn.execute('ALTER TABLE events DROP CONSTRAINT events_kind_check')
            conn.execute("ALTER TABLE events ADD CONSTRAINT events_kind_check CHECK (kind='no_helmet')")
            conn.execute('DELETE FROM schema_version WHERE version=2')
        upgraded = Database(schema=self.schema, **self.options)
        upgraded.initialize()
        self.assertEqual(self.db.event_snapshot(self.inspector, original), b'jpeg')
        other_kind = self.db.record_event(self.admin, 'camera', 'Camera', 'Factory', 1, b'jpeg', kind='no_vest')
        self.assertIsNotNone(other_kind)
        self.assertEqual(self.db.list_events(self.admin)[0]['kind'], 'no_vest')
        self.db.update_event_note(self.inspector, other_kind, 'Needs review')
        self.assertEqual(self.db.list_events(self.admin)[0]['note'], 'Needs review')
        self.assertIsNone(self.db.list_events(self.admin)[0]['reviewed_at'])
        with self.assertRaises(AccessDenied):
            self.db.delete_events(self.inspector, [other_kind])
        self.assertEqual(self.db.delete_events(self.admin, [original, other_kind]), 2)
        self.assertEqual(self.db.list_events(self.admin), [])
        with self.assertRaises(ValueError):
            self.db.event_snapshot(self.inspector, original)

    def test_journal_lookahead_does_not_change_export_page_size(self):
        with self.db.connect() as conn:
            conn.execute("INSERT INTO events(camera_id,camera_name,workspace_name,object_count,snapshot) "
                         "SELECT 'camera','Camera','Factory',1,%s FROM generate_series(1,101)", (b'jpeg',))
        self.assertEqual(len(self.db.list_events(self.inspector)), 100)
        page = self.db.list_events(self.inspector, limit=101)
        self.assertEqual(len(page), 101)
        remaining = self.db.list_events(self.inspector, before=page[99]['id'], limit=101)
        self.assertEqual(len(remaining), 1)
        with self.assertRaises(ValueError):
            self.db.list_events(self.inspector, limit=10000)


if __name__ == '__main__':
    unittest.main()
