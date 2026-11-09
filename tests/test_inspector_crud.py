"""Inspector lifecycle regressions against PostgreSQL in disposable schemas."""

from copy import deepcopy
import os
from pathlib import Path
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql

from safety_monitor.database import AccessDenied, Database, DatabaseWorkspaceStore, verify_password


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_DB') == '1', 'PostgreSQL integration tests are opt-in')
class InspectorCrudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        cls.options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                           port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                           user='postgres', password=os.environ['POSTGRES_PASSWORD'])

    def setUp(self):
        self.schema = 'test_inspector_' + uuid4().hex
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = self.new_database()
        self.db.initialize()
        self.admin = self.db.login('admin', '12345')
        self.inspector = self.db.login('daniyal', '12345')
        self.inspector_id = self.db.user(self.inspector).id
        self.store = DatabaseWorkspaceStore(self.db, self.admin)

    def cleanup_schema(self):
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def new_database(self):
        return Database(schema=self.schema, **self.options)

    def account(self, user_id=None):
        with self.db.connect() as conn:
            return conn.execute('SELECT * FROM users WHERE id=%s',
                                (self.inspector_id if user_id is None else user_id,)).fetchone()

    def assigned_records(self):
        other = self.db.create_inspector(self.admin, 'second', 'Second Inspector', 'other123')
        worker = self.db.create_worker(self.admin, 'First', 'Worker')
        zone = self.store.add_workspace('Factory', inspector_ids=[self.inspector_id, other.id], worker_ids=[worker])
        self.store.add_workspace('Another factory', inspector_ids=[self.inspector_id], worker_ids=[worker])
        camera = self.store.add_camera(zone.id, 'Entrance', source=0)
        event = self.db.record_event(self.inspector, camera.id, camera.name, zone.name, 1, b'event snapshot')
        self.db.review_event(self.inspector, event, 'Inspection complete')
        return other, worker, zone, camera, event

    def test_update_requires_admin_validates_fields_and_cannot_modify_admin(self):
        original = self.account()
        admin_id = self.db.user(self.admin).id
        for token in (self.inspector, 'invalid token'):
            with self.subTest(token=token == self.inspector):
                with self.assertRaises(AccessDenied):
                    self.db.update_inspector(token, self.inspector_id, 'changed', 'Changed')
                with self.assertRaises(AccessDenied):
                    self.db.delete_inspector(token, self.inspector_id)
        for target in (admin_id, 999999, 0, -1, True, '2'):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    self.db.update_inspector(self.admin, target, 'changed', 'Changed')
                with self.assertRaises(ValueError):
                    self.db.delete_inspector(self.admin, target)
        for username, name, password in [('ab', 'Valid', None), ('bad login', 'Valid', None),
                                         ('valid', '', None), ('valid', 'x' * 121, None),
                                         ('valid', 'Valid', '1234'), ('valid', 'Valid', '     ')]:
            with self.subTest(username=username, name=name[:10], password=password):
                with self.assertRaises(ValueError):
                    self.db.update_inspector(self.admin, self.inspector_id, username, name, password)
        self.assertEqual(self.account(), original)
        self.assertTrue(self.db.user(self.admin).is_admin)

    def test_rename_and_password_update_preserve_identity_preferences_and_active_sessions(self):
        zone = self.store.add_workspace('Assigned', inspector_ids=[self.inspector_id])
        self.db.save_preferences(self.inspector, {'theme': 'Dark'})
        peer = self.new_database()
        peer_token = peer.login('daniyal', '12345')
        original_hash = self.account()['password_hash']
        for password in (None, ''):
            changed = self.db.update_inspector(self.admin, self.inspector_id, '  RENAMED  ',
                                               '  New Inspector Name  ', password)
            self.assertEqual((changed.id, changed.username, changed.display_name, changed.role),
                             (self.inspector_id, 'renamed', 'New Inspector Name', 'inspector'))
            self.assertEqual(peer.user(peer_token), changed)
            self.assertEqual(self.account()['password_hash'], original_hash)
            self.assertEqual(self.db.user(self.db.login('renamed', '12345')), changed)
        with self.assertRaises(AccessDenied):
            self.db.login('daniyal', '12345')
        document, _ = peer.load_config(peer_token)
        self.assertEqual(document['settings']['theme'], 'Dark')
        self.assertEqual([item['id'] for item in document['workspaces']], [zone.id])
        self.db.update_inspector(self.admin, self.inspector_id, 'renamed', 'New Inspector Name', 'new password')
        with self.assertRaises(AccessDenied):
            self.db.login('renamed', '12345')
        self.assertEqual(self.db.user(self.db.login('renamed', 'new password')).id, self.inspector_id)
        self.assertNotEqual(self.account()['password_hash'], original_hash)
        self.assertTrue(verify_password('new password', self.account()['password_hash']))

    def test_duplicate_active_login_rolls_back_all_account_changes(self):
        other = self.db.create_inspector(self.admin, 'second', 'Second', 'other123')
        original = self.account()
        for username in (' SECOND ', 'AdMiN'):
            with self.subTest(username=username):
                with self.assertRaisesRegex(ValueError, 'логин уже занят'):
                    self.db.update_inspector(self.admin, self.inspector_id, username, 'Must not persist', 'new123')
                self.assertEqual(self.account(), original)
        with self.assertRaisesRegex(ValueError, 'логин уже занят'):
            self.db.create_inspector(self.admin, 'SECOND', 'Another', 'other123')
        self.assertEqual({user.id for user in self.db.list_inspectors(self.admin)}, {self.inspector_id, other.id})

    def test_delete_removes_assignments_preserves_workers_cameras_and_reviewer_history(self):
        other, worker, zone, camera, event = self.assigned_records()
        before, revision = self.db.load_config(self.admin)
        history = self.db.list_events(self.admin)
        stale = self.store.fork()
        self.db.delete_inspector(self.admin, self.inspector_id)
        expected = deepcopy(before)
        for workspace in expected['workspaces']:
            workspace['inspector_ids'].remove(self.inspector_id)
        self.assertEqual(self.db.load_config(self.admin), (expected, revision + 1))
        self.assertEqual(self.db.list_events(self.admin), history)
        self.assertEqual(self.db.event_snapshot(self.admin, event), b'event snapshot')
        self.assertEqual(self.db.get_worker(self.admin, worker)['first_name'], 'First')
        self.assertEqual([user.id for user in self.db.list_inspectors(self.admin)], [other.id])
        self.assertIsNotNone(self.account()['deleted_at'])
        with self.db.connect() as conn:
            row = conn.execute('SELECT reviewed_by,note FROM events WHERE id=%s', (event,)).fetchone()
        self.assertEqual(row, {'reviewed_by': self.inspector_id, 'note': 'Inspection complete'})
        with self.assertRaises(ValueError):
            stale.save()
        self.store.reload()
        self.assertEqual(self.store.get_workspace(zone.id).cameras[0].id, camera.id)
        with self.assertRaises(ValueError):
            self.store.update_workspace(zone.id, zone.name, [self.inspector_id], [worker])
        with self.assertRaises(ValueError):
            self.db.delete_inspector(self.admin, self.inspector_id)
        with self.assertRaises(ValueError):
            self.db.update_inspector(self.admin, self.inspector_id, 'revive', 'Must stay deleted')
        self.assertEqual(self.db.load_config(self.admin), (expected, revision + 1))

    def test_delete_revokes_login_sessions_and_background_recorders_in_another_instance(self):
        _, worker, zone, camera, event = self.assigned_records()
        peer = self.new_database()
        peer_token = peer.login('daniyal', '12345')
        recorder_session = peer.login('daniyal', '12345')
        recorder = peer.recording_token(recorder_session)
        peer.logout(recorder_session)
        # A recorder can drain after logout while its account remains active.
        self.assertIsNotNone(peer.record_event(recorder, camera.id, camera.name, zone.name,
                                              1, b'pending frame', kind='no_vest'))
        self.db.delete_inspector(self.admin, self.inspector_id)
        denied = {
            'login': lambda: peer.login('daniyal', '12345'),
            'local session': lambda: self.db.user(self.inspector),
            'other process session': lambda: peer.user(peer_token),
            'configuration': lambda: peer.load_config(peer_token),
            'journal': lambda: peer.list_events(peer_token),
            'snapshot': lambda: peer.event_snapshot(peer_token, event),
            'review': lambda: peer.review_event(peer_token, event, 'Denied'),
            'note': lambda: peer.update_event_note(peer_token, event, 'Denied'),
            'workers': lambda: peer.list_workers(peer_token),
            'worker photo': lambda: peer.get_worker(peer_token, worker),
            'inspectors': lambda: peer.list_inspectors(peer_token),
            'preferences': lambda: peer.save_preferences(peer_token, {'theme': 'Dark'}),
            'new recorder': lambda: peer.recording_token(peer_token),
            'existing recorder': lambda: peer.record_event(recorder, camera.id, camera.name, zone.name,
                                                           1, b'denied frame', kind='no_mask'),
        }
        for operation, call in denied.items():
            with self.subTest(operation=operation):
                with self.assertRaises(AccessDenied):
                    call()
        self.assertEqual(len(self.db.list_events(self.admin)), 2)
        peer.release_recording_token(recorder)

    def test_deleted_login_can_be_reused_without_inheriting_identity_or_access(self):
        self.store.add_workspace('Original job', inspector_ids=[self.inspector_id])
        self.db.save_preferences(self.inspector, {'theme': 'Dark'})
        self.db.delete_inspector(self.admin, self.inspector_id)
        replacement = self.db.create_inspector(self.admin, ' DANIYAL ', 'Replacement', 'different123')
        self.assertNotEqual(replacement.id, self.inspector_id)
        token = self.db.login('daniyal', 'different123')
        document, _ = self.db.load_config(token)
        self.assertEqual(document['workspaces'], [])
        self.assertEqual(document['settings']['theme'], 'Light')
        self.assertEqual(self.db.list_inspectors(self.admin), [replacement])
        with self.assertRaises(AccessDenied):
            self.db.login('daniyal', '12345')
        with self.assertRaises(AccessDenied):
            self.db.user(self.inspector)
        self.new_database().initialize()
        self.assertEqual(self.db.user(self.db.login('daniyal', 'different123')), replacement)
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id,deleted_at FROM users WHERE username='daniyal' ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIsNotNone(rows[0]['deleted_at'])
        self.assertIsNone(rows[1]['deleted_at'])

    def test_renamed_or_deleted_seed_is_not_recreated_during_initialize(self):
        self.db.update_inspector(self.admin, self.inspector_id, 'renamed', 'Renamed seed', 'new123')
        original = self.account()
        config = self.db.load_config(self.admin)
        self.new_database().initialize()
        self.assertEqual(self.account(), original)
        self.assertEqual(self.db.load_config(self.admin), config)
        with self.assertRaises(AccessDenied):
            self.db.login('daniyal', '12345')
        self.assertEqual(self.db.user(self.db.login('renamed', 'new123')).id, self.inspector_id)
        self.db.delete_inspector(self.admin, self.inspector_id)
        deleted = self.account()
        self.new_database().initialize()
        self.new_database().initialize()
        self.assertEqual(self.account(), deleted)
        self.assertEqual(self.db.list_inspectors(self.admin), [])
        self.assertEqual(self.db.load_config(self.admin), config)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM users').fetchone()['n'], 2)

    def test_failed_assignment_update_rolls_back_account_deletion(self):
        self.assigned_records()
        original_account = self.account()
        original_config = self.db.load_config(self.admin)
        original_history = self.db.list_events(self.admin)
        with self.db.connect() as conn:
            conn.execute("CREATE FUNCTION reject_config_update() RETURNS trigger LANGUAGE plpgsql AS $$ "
                         "BEGIN RAISE EXCEPTION 'simulated configuration failure'; END $$")
            conn.execute('CREATE TRIGGER reject_config_update BEFORE UPDATE ON application_config '
                         'FOR EACH ROW EXECUTE FUNCTION reject_config_update()')
        try:
            with self.assertRaises(psycopg.errors.RaiseException):
                self.db.delete_inspector(self.admin, self.inspector_id)
        finally:
            with self.db.connect() as conn:
                conn.execute('DROP TRIGGER reject_config_update ON application_config')
                conn.execute('DROP FUNCTION reject_config_update()')
        self.assertEqual(self.account(), original_account)
        self.assertEqual(self.db.load_config(self.admin), original_config)
        self.assertEqual(self.db.list_events(self.admin), original_history)
        self.assertEqual(self.db.user(self.inspector).id, self.inspector_id)
        self.assertEqual(self.db.user(self.db.login('daniyal', '12345')).id, self.inspector_id)

    def test_upgrade_from_previous_schema_keeps_accounts_config_workers_and_events(self):
        self.assigned_records()
        self.db.save_preferences(self.inspector, {'theme': 'Dark'})
        config = self.db.load_config(self.admin)
        history = self.db.list_events(self.admin)
        with self.db.connect() as conn:
            users = conn.execute('SELECT id,username,display_name,role,password_hash,created_at '
                                 'FROM users ORDER BY id').fetchall()
            workers = conn.execute('SELECT * FROM workers ORDER BY id').fetchall()
            preferences = conn.execute('SELECT * FROM user_preferences ORDER BY user_id').fetchall()
            # Reconstruct the immediately preceding schema in this test only.
            conn.execute('DROP INDEX users_active_username_key')
            conn.execute('ALTER TABLE users DROP COLUMN deleted_at')
            conn.execute('ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username)')
            conn.execute('DELETE FROM schema_version WHERE version=5')
        for _ in range(2):
            self.new_database().initialize()
            with self.db.connect() as conn:
                self.assertEqual(conn.execute('SELECT id,username,display_name,role,password_hash,created_at '
                                              'FROM users ORDER BY id').fetchall(), users)
                self.assertEqual(conn.execute('SELECT * FROM workers ORDER BY id').fetchall(), workers)
                self.assertEqual(conn.execute('SELECT * FROM user_preferences ORDER BY user_id').fetchall(), preferences)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM schema_version WHERE version=5')
                                 .fetchone()['n'], 1)
            self.assertEqual(self.db.load_config(self.admin), config)
            self.assertEqual(self.db.list_events(self.admin), history)
            self.assertEqual(self.db.user(self.db.login('daniyal', '12345')).id, self.inspector_id)
        self.db.delete_inspector(self.admin, self.inspector_id)
        replacement = self.db.create_inspector(self.admin, 'daniyal', 'New after upgrade', 'new123')
        self.assertNotEqual(replacement.id, self.inspector_id)


if __name__ == '__main__':
    unittest.main()
