import csv
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from safety_monitor.journal_export import export_journal


class JournalExportTests(unittest.TestCase):
    def test_all_filtered_pages_are_exported_with_readable_kinds(self):
        row = dict(occurred_at=datetime.now(timezone.utc), workspace_name='Factory', camera_name='Camera',
                   kind='no_vest', object_count=1, reviewed_at=None, reviewer=None, note='=1+1')
        database = Mock()
        database.list_events.side_effect = [[dict(row, id=i) for i in range(101, 1, -1)], [dict(row, id=1)]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.csv'
            self.assertEqual(export_journal(database, 'token', path, 'Factory', 'new'), 101)
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.reader(handle, delimiter=';'))
            self.assertEqual(len(rows), 102)
            self.assertEqual(rows[0][4], 'Вид нарушения')
            self.assertEqual(rows[1][4], 'Отсутствие сигнального жилета')
            self.assertEqual(rows[1][-1], "'=1+1")
            database.list_events.assert_any_call('token', 'Factory', 'new', 2)

    def test_failed_export_preserves_existing_file(self):
        database = Mock()
        database.list_events.side_effect = OSError('Database unavailable')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.csv'
            path.write_bytes(b'previous-export')
            with self.assertRaises(OSError):
                export_journal(database, 'token', path)
            self.assertEqual(path.read_bytes(), b'previous-export')
            self.assertEqual(list(Path(directory).iterdir()), [path])
