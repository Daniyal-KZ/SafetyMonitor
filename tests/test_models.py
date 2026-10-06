"""Storage persistence and camera validation without network connections."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from safety_monitor.models import (
    Camera, MODULE_LABELS, Rules, WorkspaceStore, source_label, validate_source,
)


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "data" / "config.json"
        self.store = WorkspaceStore(self.path)

    def test_empty_start_has_no_cameras_or_generated_data(self) -> None:
        self.assertEqual(self.store.workspaces, [])
        self.assertFalse(self.path.exists())
        workspace = self.store.add_workspace("  Производство  ")
        self.assertEqual(workspace.name, "Производство")
        self.assertEqual(workspace.cameras, [])
        self.assertTrue(self.path.is_file())

    def test_persistence_roundtrip_preserves_ids_rules_and_settings(self) -> None:
        workspace = self.store.add_workspace("Склад")
        camera = self.store.add_camera(
            workspace.id, "CAM-01", " Ворота ",
            "rtsp://user:password@192.168.1.10:554/Streaming/Channels/101", "udp", False,
        )
        camera.rules.helmet = True
        camera.rules.gloves = True
        self.store.save()
        self.store.update_settings(theme="Dark", view_columns=3, auto_connect=False, reconnect_seconds=10)
        restored = WorkspaceStore(str(self.path))
        self.assertEqual(asdict(restored.workspaces[0]), asdict(workspace))
        self.assertEqual(restored.settings, self.store.settings)
        self.assertIsNot(restored.workspaces[0].cameras[0].rules, camera.rules)

    def test_names_are_case_insensitive_and_camera_scope_is_per_zone(self) -> None:
        first = self.store.add_workspace("Склад")
        second = self.store.add_workspace("Цех")
        with self.assertRaises(ValueError):
            self.store.add_workspace("  склад  ")
        with self.assertRaises(ValueError):
            self.store.rename_workspace(second.id, "СКЛАД")
        camera = self.store.add_camera(first.id, "CAM-01", source=0)
        with self.assertRaises(ValueError):
            self.store.add_camera(first.id, " cam-01 ", source=1)
        other = self.store.add_camera(second.id, "CAM-01", source=1)
        camera.rules.vest = True
        self.assertFalse(other.rules.vest)
        for name in ("", "  \t ", None):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.store.add_workspace(name)

    def test_update_preserves_rules_and_remove_operations_persist(self) -> None:
        workspace = self.store.add_workspace("Склад")
        camera = self.store.add_camera(workspace.id, "Камера", source=0)
        camera.rules.boots = True
        rules = camera.rules
        self.store.update_camera(
            workspace.id, camera.id, name="Камера 2", description="Вход",
            source="rtsp://camera.example/live", transport="tcp", enabled=False,
        )
        self.assertIs(camera.rules, rules)
        self.assertTrue(camera.rules.boots)
        self.store.rename_workspace(workspace.id, "Новая зона")
        self.assertEqual(WorkspaceStore(self.path).workspaces[0].name, "Новая зона")
        self.store.remove_camera(workspace.id, camera.id)
        self.assertEqual(WorkspaceStore(self.path).workspaces[0].cameras, [])
        self.store.remove_workspace(workspace.id)
        self.assertEqual(WorkspaceStore(self.path).workspaces, [])

    def test_atomic_replace_failure_preserves_file_and_memory(self) -> None:
        workspace = self.store.add_workspace("Склад")
        camera = self.store.add_camera(workspace.id, "Камера", source=0)
        original = self.path.read_bytes()
        with patch("safety_monitor.models.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.add_workspace("Цех")
            with self.assertRaises(OSError):
                self.store.remove_camera(workspace.id, camera.id)
            with self.assertRaises(OSError):
                self.store.rename_workspace(workspace.id, "Другое имя")
            with self.assertRaises(OSError):
                self.store.update_camera(
                    workspace.id, camera.id, name="Другая камера", description="", source=1,
                    transport="udp", enabled=False,
                )
            with self.assertRaises(OSError):
                self.store.update_settings(theme="Dark")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(self.store.workspaces), 1)
        self.assertIs(workspace.cameras[0], camera)
        self.assertEqual(workspace.name, "Склад")
        self.assertEqual(camera.name, "Камера")
        self.assertEqual(camera.source, 0)
        self.assertEqual(self.store.settings["theme"], "Light")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_invalid_file_is_never_overwritten(self) -> None:
        self.path.parent.mkdir(parents=True)
        for invalid in ("{broken", '{"version": 9, "workspaces": []}', '{"version": 1, "workspaces": "bad"}'):
            with self.subTest(invalid=invalid):
                self.path.write_text(invalid, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Исходный файл сохранён"):
                    WorkspaceStore(self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), invalid)

    def test_schema_rejects_wrong_boolean_duplicate_ids_and_unknown_modules(self) -> None:
        workspace = self.store.add_workspace("Склад")
        self.store.add_camera(workspace.id, "Камера", source=0)
        original = json.loads(self.path.read_text(encoding="utf-8"))
        mutations = (
            lambda data: data["workspaces"][0]["cameras"][0].update(enabled="false"),
            lambda data: data["workspaces"][0]["cameras"][0].update(id=workspace.id),
            lambda data: data["workspaces"][0]["cameras"][0]["rules"].update(helmet="true"),
            lambda data: data["workspaces"][0]["cameras"][0]["rules"].update(fake=True),
        )
        for mutate in mutations:
            data = json.loads(json.dumps(original))
            mutate(data)
            content = json.dumps(data)
            self.path.write_text(content, encoding="utf-8")
            with self.assertRaises(ValueError):
                WorkspaceStore(self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_invalid_settings_never_change_saved_values(self) -> None:
        self.store.add_workspace("Склад")
        original = self.path.read_bytes()
        for values in (
            {"theme": "invalid"}, {"view_columns": 0}, {"view_columns": 4}, {"view_columns": True},
            {"auto_connect": "false"}, {"reconnect_seconds": 0}, {"unknown": 1},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.store.update_settings(**values)
                self.assertEqual(self.path.read_bytes(), original)

    def test_unavailable_saved_file_does_not_block_loading_or_other_changes(self) -> None:
        source = Path(self.directory.name) / "recording.mp4"
        source.write_bytes(b"local fixture")
        workspace = self.store.add_workspace("Склад")
        camera = self.store.add_camera(workspace.id, "Запись", source=str(source))
        source.unlink()
        restored = WorkspaceStore(self.path)
        self.assertEqual(restored.workspaces[0].cameras[0].source, str(source))
        restored.update_settings(theme="Dark")
        restored.rename_workspace(workspace.id, "Склад 2")
        with self.assertRaises(ValueError):
            restored.add_camera(workspace.id, "Новая запись", source=str(source))
        restored.update_camera(
            workspace.id, camera.id, name="Другое имя", description="", source=str(source),
            transport="tcp", enabled=False,
        )
        self.assertEqual(restored.workspaces[0].cameras[0].name, "Другое имя")
        with self.assertRaises(ValueError):
            restored.update_camera(
                workspace.id, camera.id, name="Другое имя", description="", source=str(source.parent / "absent.mp4"),
                transport="tcp", enabled=True,
            )

    def test_memory_store_does_not_write_configuration(self) -> None:
        store = WorkspaceStore()
        workspace = store.add_workspace("Цех")
        store.add_camera(workspace.id, "USB", source=0)
        store.save()
        self.assertIsNone(store.path)


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
    def test_all_modules_start_off_and_cameras_have_independent_rules(self) -> None:
        first, second = Camera("Первый"), Camera("Второй")
        self.assertEqual(set(asdict(Rules())), set(MODULE_LABELS))
        self.assertTrue(all(value is False for value in asdict(first.rules).values()))
        first.rules.helmet = True
        self.assertFalse(second.rules.helmet)


if __name__ == "__main__":
    unittest.main()
