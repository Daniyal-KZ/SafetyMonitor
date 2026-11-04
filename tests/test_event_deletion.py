import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from safety_monitor.events import EventStore


class EventDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = EventStore(Path(self.temp.name) / "events")

    def add_event(self):
        return self.store.add(occurred_at="2026-10-28T12:00:00+05:00", camera_id="a",
                              camera_name="Camera", workspace="Zone", track_id=1,
                              missing=("helmet",), frame=np.zeros((10, 10, 3), dtype=np.uint8), details={})

    def test_only_selected_events_and_photos_are_deleted(self):
        selected, kept = self.add_event(), self.add_event()
        paths = {row["id"]: self.store.snapshot_path(row) for row in self.store.query()[0]}
        self.assertEqual(self.store.delete([selected, selected, "unknown"]), 1)
        self.assertEqual([row["id"] for row in self.store.query()[0]], [kept])
        self.assertFalse(paths[selected].exists())
        self.assertTrue(paths[kept].exists())

    def test_missing_photo_does_not_block_deletion(self):
        selected = self.add_event()
        self.store.snapshot_path(self.store.query()[0][0]).unlink()
        self.assertEqual(self.store.delete([selected]), 1)

    def test_failed_photo_deletion_preserves_event_for_retry(self):
        selected = self.add_event()
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                self.store.delete([selected])
        self.assertEqual(self.store.query()[1], 1)
        self.assertEqual(self.store.delete([selected]), 1)

    def test_path_outside_photos_is_rejected(self):
        selected = self.add_event()
        protected = Path(self.temp.name) / "private.txt"
        protected.write_text("keep")
        with self.store.connection() as connection:
            connection.execute("UPDATE events SET snapshot = ? WHERE id = ?", (str(protected), selected))
        with self.assertRaises(ValueError):
            self.store.delete([selected])
        self.assertEqual(protected.read_text(), "keep")
        self.assertEqual(self.store.query()[1], 1)

    def test_journal_selection_refresh_cancel_and_delete(self):
        import customtkinter as ctk
        from tkinter import TclError
        from safety_monitor.journal import EventJournal

        try:
            root = ctk.CTk()
        except TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        root.withdraw()
        self.addCleanup(root.destroy)
        selected, kept = self.add_event(), self.add_event()
        journal = EventJournal(root, self.store, None, lambda: None)
        self.addCleanup(journal.close)
        journal.toggle_row(selected)
        journal.refresh()
        self.assertEqual(journal.table.set(selected, "check"), "☑")
        with patch("safety_monitor.journal.messagebox.askyesno", return_value=False):
            journal.delete_selected()
        self.assertEqual(self.store.query()[1], 2)
        with patch("safety_monitor.journal.messagebox.askyesno", return_value=True):
            journal.delete_selected()
        self.assertEqual(set(journal._rows), {kept})
        self.assertFalse(journal._checked)
        self.assertEqual(journal.delete_button.cget("state"), "disabled")
        journal.toggle_all()
        self.assertEqual(journal._checked, {kept})
        journal.apply_filter()
        self.assertFalse(journal._checked)


if __name__ == "__main__":
    unittest.main()
