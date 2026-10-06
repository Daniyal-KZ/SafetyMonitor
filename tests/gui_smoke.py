"""Opt-in real-window check using only temporary configuration/video files."""

from __future__ import annotations

from pathlib import Path
import ctypes
import sys
import tempfile
import time
import traceback

import cv2
import customtkinter as ctk
import numpy as np
from PIL import ImageGrab

from safety_monitor.app import App
from safety_monitor.models import WorkspaceStore


def main() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        directory = Path(temp_dir)
        config = directory / "config.json"
        video = directory / "check.avi"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 12, (320, 240))
        assert writer.isOpened()
        try:
            for index in range(12):
                writer.write(np.full((240, 320, 3), (index * 12, 80, 120), dtype=np.uint8))
        finally:
            writer.release()
        app = App(config_path=config)
        failures: list[str] = []
        app.report_callback_exception = lambda *args: failures.append("".join(traceback.format_exception(*args)))
        artifacts = Path(".artifacts")
        artifacts.mkdir(exist_ok=True)

        def pump(seconds: float = 0.2) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                app.update()
                time.sleep(0.015)
            assert not failures, "\n".join(failures)

        def capture(name: str, widget=None) -> None:
            # Capture the application's native window, even when covered.
            # Never fall back to capturing a desktop rectangle.
            if sys.platform != "win32":
                return
            widget = widget if widget is not None else app
            get_ancestor = ctypes.windll.user32.GetAncestor
            get_ancestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            get_ancestor.restype = ctypes.c_void_p
            hwnd = get_ancestor(widget.winfo_id(), 2)
            if not hwnd:
                raise RuntimeError("Не найдено окно приложения для снимка")
            ImageGrab.grab(window=hwnd).save(artifacts / name)

        try:
            app.geometry("1280x800+20+20")
            pump()
            dashboard = app.screen
            assert not app.store.workspaces and dashboard.workspace is None
            assert app.cget("menu")
            capture("classic-empty.png")
            dashboard.workspace_manager.create_workspace()
            pump()
            dialog = dashboard.workspace_manager.dialog
            dialog.submit()
            assert dialog.error_label.cget("text")
            dialog.name_entry.insert(0, "Производственная зона")
            dialog.submit()
            pump()
            workspace = dashboard.workspace
            assert workspace is not None and not workspace.cameras
            dashboard.add_camera()
            pump()
            dialog = dashboard._dialogs[-1]
            assert dialog.source_menu.get() == "IP-камера (RTSP)"
            dialog.name_entry.insert(0, "Вход")
            dialog.submit()
            assert dialog.error_label.cget("text")
            capture("classic-connection.png", dialog)
            dialog.source_menu.set("Видеофайл")
            dialog._change_source("Видеофайл")
            dialog.source_entry.insert(0, str(video))
            dialog.submit()
            pump(0.8)
            first = workspace.cameras[0]
            tile = dashboard.tiles[first.id]
            assert tile.state == "streaming" and tile.cap.isOpened()
            cap = tile.cap
            width, height = tile._image.cget("size")
            assert abs(width / height - 4 / 3) < 0.03
            second = app.store.add_camera(workspace.id, "Периметр", source=str(video), enabled=False)
            dashboard._render_wall()
            pump()
            assert dashboard.tiles[first.id] is tile and cap.isOpened(), "Adding a camera interrupted an existing stream"
            dashboard._select_camera(first.id)
            pump()
            dashboard.camera_settings()
            pump()
            settings = dashboard._dialogs[-1]
            assert len(settings.module_vars) == 7
            settings.module_vars["helmet"].set(True)
            settings.module_vars["gloves"].set(True)
            settings.module_vars["boots"].set(True)
            capture("classic-modules.png", settings)
            settings.submit()
            pump()
            loaded = WorkspaceStore(config)
            assert loaded.workspaces[0].cameras[0].rules.helmet
            assert loaded.workspaces[0].cameras[0].rules.gloves
            assert loaded.workspaces[0].cameras[0].rules.boots
            assert not loaded.workspaces[0].cameras[1].rules.helmet
            capture("classic-main.png")
            dashboard.tabs.set("Камеры")
            pump()
            assert dashboard.camera_table.selection() == (first.id,)
            capture("classic-cameras.png")
            dashboard.tabs.set("Наблюдение")

            dashboard.application_settings()
            pump()
            settings = dashboard._dialogs[-1]
            settings.theme_menu.set("Тёмная")
            settings.columns_menu.set("1")
            settings.auto_connect_var.set(False)
            settings.submit()
            pump()
            assert ctk.get_appearance_mode() == "Dark"
            assert dashboard._layout_columns == 1
            assert tile.cap is cap and cap.isOpened()
            dashboard.application_settings()
            pump()
            settings = dashboard._dialogs[-1]
            settings.theme_menu.set("Светлая")
            settings.columns_menu.set("2")
            settings.submit()
            pump()
            app.geometry("900x520+20+20")
            pump()
            assert 1 <= dashboard._layout_columns <= 2
            assert dashboard.content.winfo_width() > 550
            ctk.set_widget_scaling(1.25)
            ctk.set_window_scaling(1.25)
            pump()
            assert dashboard.tiles[first.id].video_area.winfo_height() > 60
            ctk.set_widget_scaling(1.0)
            ctk.set_window_scaling(1.0)
            app.geometry("1280x800+20+20")

            other = app.store.add_workspace("Склад")
            dashboard.workspace_manager.select_workspace(other.id)
            pump()
            assert not cap.isOpened(), "Switching zones did not release capture"
            assert not dashboard.tiles
            dashboard.workspace_manager.select_workspace(workspace.id)
            pump()
            assert all(value.state == "disconnected" for value in dashboard.tiles.values())
            dashboard.tiles[second.id].connect()
            assert dashboard.tiles[second.id].state == "disconnected"
            dashboard.selected_camera_id = None
            dashboard.connect_cameras()
            pump(0.5)
            tile = dashboard.tiles[first.id]
            assert tile.state == "streaming"
            cap = tile.cap
            tile.disconnect()
            assert tile._cached_frame is None
            pump()
            assert not cap.isOpened()

            # Selecting a camera from the full table must switch the wall page.
            for index in range(8):
                app.store.add_camera(workspace.id, f"Пост {index + 1}", source=str(video), enabled=False)
            dashboard._render_wall()
            last = workspace.cameras[-1]
            dashboard._select_camera(last.id)
            pump()
            assert dashboard._page == 1 and last.id in dashboard.tiles
            assert dashboard.camera_table.selection() == (last.id,)
            tiles = list(dashboard.tiles.values()) + dashboard._retired_tiles
            app.close()
            assert all(value.wait_closed(timeout=0) for value in tiles)
            app.close()
            restored = WorkspaceStore(config)
            assert len(restored.workspaces) == 2 and len(restored.workspaces[0].cameras) == 10
            print("GUI passed: empty startup, classic menus, RTSP form, live file, module persistence, themes, DPI, camera table, paging, disconnect, zone switch and shutdown.")
        finally:
            app.close()


if __name__ == "__main__":
    main()
