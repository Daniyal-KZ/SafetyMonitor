"""Domain validation and rollback; production persistence is PostgreSQL."""
from __future__ import annotations

from dataclasses import asdict
import copy
from pathlib import Path
import tempfile
import unittest

from safety_monitor.models import (
    Camera, MODULE_LABELS, Rules, WorkspaceStore, source_label, validate_source,
)


class TestStore(WorkspaceStore):
    """Test-only persistence boundary; no alternative application datastore."""

    def __init__(self):
        super().__init__()
        self.snapshot = None
        self.fail_save = False

    def save(self):
        if self.fail_save:
            raise OSError("storage failure")
        document = {"version": 1, "settings": self.settings,
                    "workspaces": [asdict(workspace) for workspace in self.workspaces]}
        self._decode(document)
        self.snapshot = copy.deepcopy(document)

    def restored(self):
        store = TestStore()
        store.workspaces, store.settings = self._decode(copy.deepcopy(self.snapshot))
        return store


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = TestStore()

    def test_empty_start_and_roundtrip_preserve_ids_rules_settings(self):
        self.assertEqual(self.store.workspaces, [])
        workspace = self.store.add_workspace("  Production  ")
        self.assertEqual(workspace.name, "Production")
        camera = self.store.add_camera(workspace.id, "CAM-01", " Camera ",
                                      "rtsp://user:password@camera.example/live", "udp", False)
        camera.rules.gloves = True
        self.store.update_settings(theme="Dark", view_columns=3, auto_connect=False,
                                   reconnect_seconds=10)
        restored = self.store.restored()
        self.assertEqual(asdict(restored.workspaces[0]), asdict(workspace))
        self.assertEqual(restored.settings, self.store.settings)
        self.assertIsNot(restored.workspaces[0].cameras[0].rules, camera.rules)

    def test_names_are_case_insensitive_and_camera_scope_is_per_zone(self):
        first = self.store.add_workspace("Warehouse")
        second = self.store.add_workspace("Workshop")
        with self.assertRaises(ValueError):
            self.store.add_workspace("  warehouse  ")
        with self.assertRaises(ValueError):
            self.store.rename_workspace(second.id, "WAREHOUSE")
        camera = self.store.add_camera(first.id, "CAM-01", source=0)
        with self.assertRaises(ValueError):
            self.store.add_camera(first.id, " cam-01 ", source=1)
        other = self.store.add_camera(second.id, "CAM-01", source=1)
        camera.rules.vest = True
        self.assertFalse(other.rules.vest)
        for name in ("", "  \t ", None):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.add_workspace(name)

    def test_update_preserves_rules_and_removals_are_saved(self):
        workspace = self.store.add_workspace("Warehouse")
        camera = self.store.add_camera(workspace.id, "Camera", source=0)
        camera.rules.boots = True
        rules = camera.rules
        self.store.update_camera(workspace.id, camera.id, name="Camera 2", description="Gate",
                                 source="rtsp://camera.example/live", transport="tcp", enabled=False)
        self.assertIs(camera.rules, rules)
        self.assertTrue(camera.rules.boots)
        self.store.rename_workspace(workspace.id, "New zone")
        self.assertEqual(self.store.restored().workspaces[0].name, "New zone")
        self.store.remove_camera(workspace.id, camera.id)
        self.assertEqual(self.store.restored().workspaces[0].cameras, [])
        self.store.remove_workspace(workspace.id)
        self.assertEqual(self.store.restored().workspaces, [])

    def test_persistence_failure_rolls_back_every_domain_operation(self):
        workspace = self.store.add_workspace("Warehouse")
        camera = self.store.add_camera(workspace.id, "Camera", source=0)
        original = copy.deepcopy(self.store.snapshot)
        self.store.fail_save = True
        operations = (
            lambda: self.store.add_workspace("Workshop"),
            lambda: self.store.remove_workspace(workspace.id),
            lambda: self.store.add_camera(workspace.id, "New camera", source=1),
            lambda: self.store.remove_camera(workspace.id, camera.id),
            lambda: self.store.rename_workspace(workspace.id, "Other name"),
            lambda: self.store.update_camera(workspace.id, camera.id, name="Other camera",
                                            description="", source=1, transport="udp", enabled=False),
            lambda: self.store.update_settings(theme="Dark"),
        )
        for operation in operations:
            with self.assertRaises(OSError):
                operation()
            self.assertEqual(self.store.snapshot, original)
            self.assertIs(self.store.workspaces[0], workspace)
            self.assertIs(workspace.cameras[0], camera)
            self.assertEqual(asdict(workspace), original["workspaces"][0])
            self.assertEqual(self.store.settings, original["settings"])

    def test_schema_rejects_wrong_boolean_duplicate_ids_unknown_modules(self):
        workspace = self.store.add_workspace("Warehouse")
        self.store.add_camera(workspace.id, "Camera", source=0)
        mutations = (
            lambda data: data["workspaces"][0]["cameras"][0].update(enabled="false"),
            lambda data: data["workspaces"][0]["cameras"][0].update(id=workspace.id),
            lambda data: data["workspaces"][0]["cameras"][0]["rules"].update(helmet="true"),
            lambda data: data["workspaces"][0]["cameras"][0]["rules"].update(fake=True),
            lambda data: data.update(version=9),
            lambda data: data.update(workspaces="bad"),
        )
        for mutate in mutations:
            data = copy.deepcopy(self.store.snapshot)
            mutate(data)
            with self.assertRaises(ValueError):
                WorkspaceStore._decode(data)

    def test_invalid_settings_never_change_saved_values(self):
        self.store.add_workspace("Warehouse")
        original = copy.deepcopy(self.store.snapshot)
        for values in (
            {"theme": "invalid"}, {"view_columns": 0}, {"view_columns": 4}, {"view_columns": True},
            {"auto_connect": "false"}, {"reconnect_seconds": 0}, {"unknown": 1},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.update_settings(**values)
            self.assertEqual(self.store.snapshot, original)

    def test_unavailable_saved_video_allows_loading_and_unrelated_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recording.mp4"
            source.write_bytes(b"local fixture")
            workspace = self.store.add_workspace("Warehouse")
            camera = self.store.add_camera(workspace.id, "Camera", source=str(source))
            source.unlink()
            restored = self.store.restored()
            self.assertEqual(restored.workspaces[0].cameras[0].source, str(source))
            restored.update_settings(theme="Dark")
            restored.rename_workspace(workspace.id, "Warehouse 2")
            with self.assertRaises(ValueError):
                restored.add_camera(workspace.id, "New camera", source=str(source))
            restored.update_camera(workspace.id, camera.id, name="Other name", description="",
                                   source=str(source), transport="tcp", enabled=False)
            with self.assertRaises(ValueError):
                restored.update_camera(workspace.id, camera.id, name="Other name", description="",
                                       source=str(source.parent / "absent.mp4"), transport="tcp", enabled=True)


class SourceValidationTests(unittest.TestCase):
    def test_valid_rtsp_does_not_require_network_access(self) -> None:
        for source in (
            "rtsp://192.168.1.10:554/live", "rtsps://camera.example:322/live",
            "rtsp://user:password@camera.example/Streaming/Channels/101",
            "rtsp://[2001:db8::1]:554/live", "RTSP://camera.example/live?channel=1",
        ):
            with self.subTest(source=source):
                self.assertEqual(validate_source(source), source)
        self.assertEqual(validate_source(0), 0)

    def test_invalid_sources_and_ports_have_no_credentials_in_errors(self) -> None:
        for source in (
            True, -1, "", "demo", "missing.mp4", "http://camera.example/live",
            "rtsp:///live", "rtsp://", "rtsp://camera:0/live", "rtsp://camera:65536/live",
            "rtsp://camera:/live",
            "rtsp://user:SECRET@camera:wrong/live", "rtsp://camera live",
        ):
            with self.subTest(source=source):
                with self.assertRaises(ValueError) as raised:
                    validate_source(source)
                self.assertNotIn("SECRET", str(raised.exception))

    def test_local_file_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "video sample.mp4"
            source.write_bytes(b"local fixture")
            self.assertEqual(validate_source(str(source)), str(source))
            with self.assertRaises(ValueError):
                validate_source(directory)

    def test_labels_do_not_include_username_password_or_query(self) -> None:
        self.assertEqual(
            source_label("rtsp://user:SECRET@[2001:db8::1]:554/live?token=PRIVATE"),
            "rtsp://[2001:db8::1]:554/live",
        )
        self.assertEqual(source_label(0), "USB-камера 0")
        self.assertEqual(source_label("rtsp://user:SECRET@camera:bad/live"), "RTSP-камера")


class RulesTests(unittest.TestCase):
    def test_helmet_starts_on_and_cameras_have_independent_rules(self) -> None:
        first, second = Camera("Первый"), Camera("Второй")
        self.assertEqual(set(asdict(Rules())), set(MODULE_LABELS))
        self.assertTrue(first.rules.helmet)
        self.assertTrue(all(value is False for key, value in asdict(first.rules).items() if key != "helmet"))
        first.rules.helmet = False
        self.assertTrue(second.rules.helmet)


if __name__ == "__main__":
    unittest.main()
