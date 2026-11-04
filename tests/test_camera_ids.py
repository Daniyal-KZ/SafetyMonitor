"""Stable user-facing camera numbers alongside the existing UUID identity."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path
import threading
import unittest
from uuid import uuid4

from dotenv import load_dotenv
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from safety_monitor.database import Database, DatabaseWorkspaceStore
from safety_monitor.models import Camera, Workspace, WorkspaceStore


class CameraNumberValidationTests(unittest.TestCase):
    def document(self):
        return dict(version=1, workspaces=[asdict(Workspace('Factory', cameras=[Camera('Camera', source=0)]))])

    def test_legacy_camera_without_number_remains_decodable(self):
        document = self.document()
        camera = document['workspaces'][0]['cameras'][0]
        camera.pop('display_id')
        zones, _ = WorkspaceStore._decode(document)
        self.assertIsNone(zones[0].cameras[0].display_id)
        self.assertEqual(zones[0].cameras[0].id, camera['id'])

    def test_invalid_and_duplicate_numbers_are_rejected_across_workspaces(self):
        for value in (True, False, 999, 10000, '1000', 1000.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                document = self.document()
                document['workspaces'][0]['cameras'][0]['display_id'] = value
                WorkspaceStore._decode(document)
        document = self.document()
        document['workspaces'][0]['cameras'][0]['display_id'] = 1000
        document['workspaces'].append(asdict(Workspace('Other', cameras=[Camera('Other camera', source=1, display_id=1000)])))
        with self.assertRaises(ValueError):
            WorkspaceStore._decode(document)


@unittest.skipUnless(os.getenv('SAFETY_MONITOR_TEST_DB') == '1', 'PostgreSQL integration tests are opt-in')
class CameraNumberDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(Path(__file__).resolve().parent.parent / '.env')
        cls.options = dict(host=os.getenv('SAFETY_MONITOR_DB_HOST', '127.0.0.1'),
                           port=os.getenv('SAFETY_MONITOR_DB_PORT', '5432'), dbname='safety_monitor',
                           user='postgres', password=os.environ['POSTGRES_PASSWORD'])

    def setUp(self):
        self.schema = 'test_camera_ids_' + uuid4().hex
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_schema)
        self.db = Database(schema=self.schema, **self.options)
        self.db.initialize()
        self.admin = self.db.login('admin', '12345')
        self.inspector = self.db.login('daniyal', '12345')
        self.inspector_id = self.db.user(self.inspector).id
        self.store = DatabaseWorkspaceStore(self.db, self.admin)
        self.zone = self.store.add_workspace('Factory', inspector_ids=[self.inspector_id])

    def cleanup_schema(self):
        with psycopg.connect(**self.options) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    def test_numbers_are_global_stable_and_preserve_uuid_authorization_and_history(self):
        camera = self.store.add_camera(self.zone.id, 'Entrance', source=0)
        other_zone = self.store.add_workspace('Other')
        other = self.store.add_camera(other_zone.id, 'Entrance', source=1)
        self.assertEqual((camera.display_id, other.display_id), (1000, 1001))
        original_uuid = camera.id
        event = self.db.record_event(self.inspector, camera.id, camera.name, self.zone.name, 1, b'jpeg')
        self.store.update_camera(self.zone.id, camera.id, name='Renamed', description='New description',
                                 source=7, transport='udp', enabled=False)
        self.store.reload()
        self.assertIs(self.store.get_workspace(self.zone.id).cameras[0], camera)
        self.assertEqual(camera.id, original_uuid)
        self.assertEqual(camera.display_id, 1000)
        inspector_store = DatabaseWorkspaceStore(self.db, self.inspector)
        self.assertEqual(inspector_store.workspaces[0].cameras[0].display_id, 1000)
        self.assertEqual(self.db.event_snapshot(self.inspector, event), b'jpeg')
        with self.db.connect() as conn:
            self.assertEqual(conn.execute('SELECT camera_id FROM events WHERE id=%s', (event,)).fetchone()['camera_id'],
                             original_uuid)

    def test_manual_number_changes_are_rejected_without_consuming_an_id(self):
        camera = self.store.add_camera(self.zone.id, 'Original', source=0)
        document, revision = self.db.load_config(self.admin)
        tampered = deepcopy(document)
        tampered['workspaces'][0]['cameras'][0]['display_id'] = 2345
        with self.assertRaisesRegex(ValueError, 'автоматически'):
            self.db.save_config(self.admin, tampered, revision)
        tampered = deepcopy(document)
        tampered['workspaces'][0]['cameras'].append(asdict(Camera('Forged', source=1, display_id=2345)))
        with self.assertRaisesRegex(ValueError, 'автоматически'):
            self.db.save_config(self.admin, tampered, revision)
        other = self.store.add_camera(self.zone.id, 'Automatic', source=1)
        self.assertEqual((camera.display_id, other.display_id), (1000, 1001))

    def test_omitting_existing_number_preserves_it_and_canonical_return_is_optional(self):
        camera = self.store.add_camera(self.zone.id, 'Original', source=0)
        document, revision = self.db.load_config(self.admin)
        document['workspaces'][0]['cameras'][0].pop('display_id')
        saved, revision = self.db.save_config(self.admin, document, revision, return_document=True)
        self.assertEqual(saved['workspaces'][0]['cameras'][0]['display_id'], camera.display_id)
        self.assertIs(type(self.db.save_config(self.admin, saved, revision)), int)

    def test_removal_does_not_recycle_camera_number(self):
        first = self.store.add_camera(self.zone.id, 'First', source=0)
        self.store.remove_camera(self.zone.id, first.id)
        second = self.store.add_camera(self.zone.id, 'First', source=0)
        self.assertEqual(second.display_id, first.display_id + 1)
        self.assertNotEqual(second.id, first.id)
        self.store.remove_workspace(self.zone.id)
        zone = self.store.add_workspace('New factory')
        third = self.store.add_camera(zone.id, 'Third', source=1)
        self.assertEqual(third.display_id, second.display_id + 1)

    def test_concurrent_stale_save_does_not_publish_or_consume_a_number(self):
        first, second = self.store.fork(), self.store.fork()
        gate = threading.Barrier(2)

        def create(store):
            gate.wait(timeout=5)
            try:
                return store.add_camera(self.zone.id, 'Concurrent', source=0)
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (first, second)))
        self.assertEqual(sum(result is not None for result in results), 1)
        saved = next(result for result in results if result is not None)
        self.assertEqual(saved.display_id, 1000)
        loser = first if results[0] is None else second
        self.assertEqual(loser.get_workspace(self.zone.id).cameras, [])
        loser.reload()
        self.assertEqual(loser.add_camera(self.zone.id, 'Next', source=1).display_id, 1001)

    def test_number_exhaustion_rolls_back_camera_and_config(self):
        with self.db.connect() as conn:
            conn.execute('ALTER SEQUENCE camera_display_id_seq RESTART WITH 9999')
        camera = self.store.add_camera(self.zone.id, 'Last', source=0)
        self.assertEqual(camera.display_id, 9999)
        revision = self.store.revision
        with self.assertRaisesRegex(ValueError, 'ID камер закончились'):
            self.store.add_camera(self.zone.id, 'Overflow', source=1)
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(self.zone.cameras, [camera])
        self.assertEqual(len(self.db.load_config(self.admin)[0]['workspaces'][0]['cameras']), 1)

    def test_migration_assigns_existing_cameras_once_and_keeps_assignments_and_events(self):
        first = self.store.add_camera(self.zone.id, 'First', source=0)
        other_zone = self.store.add_workspace('Second zone', inspector_ids=[self.inspector_id])
        second = self.store.add_camera(other_zone.id, 'Second', source=1)
        event = self.db.record_event(self.inspector, first.id, first.name, self.zone.name, 1, b'legacy')
        document, _ = self.db.load_config(self.admin)
        for zone in document['workspaces']:
            for camera in zone['cameras']:
                camera.pop('display_id')
        with self.db.connect() as conn:
            conn.execute('UPDATE application_config SET document=%s', (Jsonb(document),))
            conn.execute('DELETE FROM schema_version WHERE version=4')
            conn.execute('ALTER SEQUENCE camera_display_id_seq RESTART WITH 1000')
        upgraded = Database(schema=self.schema, **self.options)
        upgraded.initialize()
        migrated, revision = self.db.load_config(self.admin)
        ids = {camera['id']: camera['display_id'] for zone in migrated['workspaces'] for camera in zone['cameras']}
        self.assertEqual(ids, {first.id: 1000, second.id: 1001})
        self.assertEqual(migrated['workspaces'][0]['inspector_ids'], [self.inspector_id])
        self.assertEqual(self.db.event_snapshot(self.inspector, event), b'legacy')
        Database(schema=self.schema, **self.options).initialize()
        self.assertEqual(self.db.load_config(self.admin), (migrated, revision))
        self.store.reload()
        self.assertEqual(self.store.add_camera(self.zone.id, 'After migration', source=2).display_id, 1002)


if __name__ == '__main__':
    unittest.main()
