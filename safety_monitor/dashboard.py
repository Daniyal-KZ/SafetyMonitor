"""Classic desktop workspace, camera wall, and camera management table."""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from .dialogs import ApplicationSettingsDialog, CameraDialog, CameraSettingsDialog
from .models import Camera, MODULE_LABELS, Workspace, WorkspaceStore, source_label
from .theme import BACKGROUND, BLUE, BORDER, MUTED, SURFACE, TEXT, font
from .video import VideoPlayerFrame
from .workspaces import WorkspaceManager


class MainDashboard(ctk.CTkFrame):
    PAGE_SIZE = 9

    def __init__(self, master: ctk.CTk, store: WorkspaceStore) -> None:
        super().__init__(master, fg_color=BACKGROUND, corner_radius=0)
        self.store = store
        self.workspace: Workspace | None = None
        self.selected_camera_id: str | None = None
        self.tiles: dict[str, VideoPlayerFrame] = {}
        self._retired_tiles: list[VideoPlayerFrame] = []
        self._states: dict[str, tuple[str, str]] = {}
        self._dialogs: list[ctk.CTkToplevel] = []
        self._page = 0
        self._closed = False
        self._table_refreshing = False
        self._layout_columns = 2
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_toolbar()
        self.workspace_manager = WorkspaceManager(self, store, self.select_workspace)
        self.workspace_manager.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        self.workspace_manager.grid_propagate(False)

        self.content = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.content.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 8))
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        heading = ctk.CTkFrame(self.content, fg_color="transparent", corner_radius=0)
        heading.grid(row=0, column=0, sticky="ew", pady=(2, 4))
        heading.grid_columnconfigure(0, weight=1)
        self.workspace_label = ctk.CTkLabel(heading, text="Рабочая зона не выбрана", text_color=TEXT,
                                           font=font(16, "bold"), anchor="w")
        self.workspace_label.grid(row=0, column=0, sticky="ew", padx=4)
        self.count_label = ctk.CTkLabel(heading, text="", text_color=MUTED, font=font(12))
        self.count_label.grid(row=0, column=1, padx=4)

        self.tabs = ctk.CTkTabview(self.content, corner_radius=0, border_width=1, border_color=BORDER,
                                   fg_color=SURFACE, segmented_button_fg_color=BACKGROUND,
                                   segmented_button_selected_color=SURFACE,
                                   segmented_button_selected_hover_color=BORDER,
                                   segmented_button_unselected_color=BACKGROUND,
                                   segmented_button_unselected_hover_color=BORDER,
                                   text_color=TEXT, anchor="w")
        self.tabs.grid(row=1, column=0, sticky="nsew")
        for name in ("Наблюдение", "Камеры"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.wall = ctk.CTkScrollableFrame(self.tabs.tab("Наблюдение"), fg_color=BACKGROUND,
                                          corner_radius=0, border_width=0)
        self.wall.grid(row=0, column=0, sticky="nsew")
        self.empty_frame = ctk.CTkFrame(self.wall, fg_color="transparent", corner_radius=0)
        self.empty_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.empty_frame, text="Создайте или откройте рабочую зону.",
                                       text_color=MUTED, font=font(14))
        self.empty_label.grid(row=0, column=0, padx=16, pady=(80, 14))
        self.empty_button = self._button(self.empty_frame, "Создать рабочую зону…",
                                         self.workspace_manager.create_workspace, width=230)
        self.empty_button.grid(row=1, column=0, pady=(0, 80))
        self._build_camera_table()

        paging = ctk.CTkFrame(self.content, fg_color="transparent", corner_radius=0)
        paging.grid(row=2, column=0, sticky="ew", pady=(5, 0))
        paging.grid_columnconfigure(0, weight=1)
        self.page_label = ctk.CTkLabel(paging, text="", text_color=MUTED, font=font(12), anchor="w")
        self.page_label.grid(row=0, column=0, sticky="ew")
        self.previous_button = self._button(paging, "Назад", lambda: self.change_page(-1), width=75)
        self.previous_button.grid(row=0, column=1, padx=(6, 0))
        self.next_button = self._button(paging, "Далее", lambda: self.change_page(1), width=75)
        self.next_button.grid(row=0, column=2, padx=(6, 0))

        status = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, border_width=1, border_color=BORDER)
        status.grid(row=2, column=0, columnspan=2, sticky="ew")
        status.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(status, text="Готово", height=24, text_color=MUTED,
                                        font=font(11), anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", padx=10)
        ctk.CTkLabel(status, text="Анализ СИЗ: модель не подключена", text_color=MUTED,
                     font=font(11), height=24).grid(row=0, column=1, padx=10)
        self.content.bind("<Configure>", self._on_resize)
        self.apply_settings()
        if self.store.workspaces:
            self.workspace_manager.select_workspace(self.store.workspaces[0].id)
        else:
            self._show_empty()
        self._update_paging()
        self._update_actions()

    @staticmethod
    def _button(master, text: str, command, width: int = 130) -> ctk.CTkButton:
        return ctk.CTkButton(master, text=text, command=command, width=width, height=30,
                             corner_radius=3, fg_color=SURFACE, text_color=TEXT,
                             border_color=BORDER, border_width=1, hover_color=BORDER, font=font(12))

    def _build_toolbar(self) -> None:
        toolbar = ctk.CTkFrame(self, fg_color=BACKGROUND, corner_radius=0)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        toolbar.grid_columnconfigure(5, weight=1)
        self.add_button = self._button(toolbar, "Добавить камеру…", self.add_camera, width=150)
        self.add_button.grid(row=0, column=0, padx=(0, 6))
        self.properties_button = self._button(toolbar, "Свойства…", self.edit_camera, width=100)
        self.properties_button.grid(row=0, column=1, padx=(0, 6))
        self.modules_button = self._button(toolbar, "Модули СИЗ…", self.camera_settings, width=125)
        self.modules_button.grid(row=0, column=2, padx=(0, 16))
        self.connect_button = self._button(toolbar, "Подключить", self.connect_cameras, width=110)
        self.connect_button.grid(row=0, column=3, padx=(0, 6))
        self.disconnect_button = self._button(toolbar, "Отключить", self.disconnect_cameras, width=110)
        self.disconnect_button.grid(row=0, column=4)
        ctk.CTkLabel(toolbar, text="Колонки:", text_color=MUTED, font=font(12)).grid(row=0, column=6, padx=(12, 6))
        self.columns_menu = ctk.CTkOptionMenu(toolbar, values=["1", "2", "3"], width=62, height=30,
                                             corner_radius=3, fg_color=SURFACE, button_color=BORDER,
                                             button_hover_color=BORDER, text_color=TEXT,
                                             font=font(12), command=self._set_columns)
        self.columns_menu.grid(row=0, column=7)

    def _build_camera_table(self) -> None:
        tab = self.tabs.tab("Камеры")
        tab.grid_columnconfigure(0, weight=1)
        columns = ("name", "state", "source", "modules")
        self.camera_table = ttk.Treeview(tab, columns=columns, show="headings", selectmode="browse",
                                         style="Camera.Treeview")
        self.camera_table.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=(4, 0))
        for key, title, width in (("name", "Камера", 155), ("state", "Состояние", 150),
                                   ("source", "Источник", 300), ("modules", "Модули СИЗ", 260)):
            self.camera_table.heading(key, text=title, anchor="w")
            self.camera_table.column(key, width=width, minwidth=90, anchor="w")
        y_scroll = ctk.CTkScrollbar(tab, command=self.camera_table.yview, width=12)
        y_scroll.grid(row=0, column=1, sticky="ns", pady=4)
        x_scroll = ctk.CTkScrollbar(tab, command=self.camera_table.xview, orientation="horizontal", height=12)
        x_scroll.grid(row=1, column=0, sticky="ew", padx=4)
        self.camera_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.camera_table.bind("<<TreeviewSelect>>", self._on_table_selection)
        self.camera_table.bind("<Double-1>", lambda _event: self.edit_camera())
        self.camera_table.bind("<Button-3>", self._camera_context_menu)
        note = ctk.CTkLabel(tab, text="Выберите камеру для изменения подключения или модулей контроля.",
                            text_color=MUTED, font=font(12), anchor="w")
        note.grid(row=2, column=0, columnspan=2, sticky="ew", padx=6, pady=6)

    @property
    def selected_camera(self) -> Camera | None:
        if self.workspace is None:
            return None
        return next((camera for camera in self.workspace.cameras if camera.id == self.selected_camera_id), None)

    def select_workspace(self, workspace: Workspace, camera: Camera | None = None) -> None:
        changed = self.workspace is None or self.workspace.id != workspace.id
        self.workspace = workspace
        self.selected_camera_id = camera.id if camera else None
        self.workspace_label.configure(text=workspace.name)
        if changed:
            self._page = 0
            self._render_wall()
        elif camera is not None:
            index = workspace.cameras.index(camera)
            if index // self.PAGE_SIZE != self._page:
                self._page = index // self.PAGE_SIZE
                self._render_wall()
        self._refresh_table()
        self._highlight_selection()
        self._update_actions()

    def _show_empty(self) -> None:
        self.empty_frame.grid(row=0, column=0, columnspan=max(1, self._layout_columns), sticky="ew")
        if self.workspace is None:
            self.empty_label.configure(text="Создайте или откройте рабочую зону.")
            self.empty_button.configure(text="Создать рабочую зону…", command=self.workspace_manager.create_workspace)
        else:
            self.empty_label.configure(text="В этой рабочей зоне пока нет камер.")
            self.empty_button.configure(text="Добавить камеру…", command=self.add_camera)

    def _retire_tiles(self, keep_ids: set[str] | None = None) -> None:
        keep_ids = keep_ids or set()
        for camera_id, tile in list(self.tiles.items()):
            if camera_id in keep_ids:
                continue
            tile.close(wait=False)
            tile.destroy()
            self._retired_tiles.append(tile)
            self._states[camera_id] = "disconnected", "Не подключена"
            del self.tiles[camera_id]
        self._retired_tiles = [tile for tile in self._retired_tiles if not tile.wait_closed(timeout=0)]

    def _render_wall(self) -> None:
        self.empty_frame.grid_remove()
        cameras = self.workspace.cameras if self.workspace else []
        self._page = max(0, min(self._page, max(0, (len(cameras) - 1) // self.PAGE_SIZE)))
        visible = cameras[self._page * self.PAGE_SIZE:(self._page + 1) * self.PAGE_SIZE]
        self._retire_tiles({camera.id for camera in visible})
        for camera in visible:
            if camera.id in self.tiles:
                continue
            tile = VideoPlayerFrame(self.wall, reconnect_seconds=float(self.store.settings["reconnect_seconds"]),
                                    on_status=lambda state, detail, camera_id=camera.id:
                                    self._on_camera_status(camera_id, state, detail))
            tile.configure(height=280, width=290)
            tile.grid_propagate(False)
            self._bind_tile_selection(tile, camera.id)
            self.tiles[camera.id] = tile
            tile.set_camera(camera, self.workspace.name, auto_connect=bool(self.store.settings["auto_connect"]))
        self._place_tiles()
        if not visible:
            self._show_empty()
        self._update_paging()
        self._refresh_table()
        self._update_actions()

    def _place_tiles(self) -> None:
        for column in range(3):
            self.wall.grid_columnconfigure(column, weight=1 if column < self._layout_columns else 0,
                                            uniform="camera_wall" if column < self._layout_columns else "")
        for index, tile in enumerate(self.tiles.values()):
            tile.grid(row=index // self._layout_columns, column=index % self._layout_columns,
                       sticky="nsew", padx=4, pady=4)
        self._highlight_selection()

    def _bind_tile_selection(self, widget, camera_id: str) -> None:
        widget.bind("<Button-1>", lambda _event: self._select_camera(camera_id), add="+")
        for child in widget.winfo_children():
            self._bind_tile_selection(child, camera_id)

    def _on_resize(self, _event=None) -> None:
        width = self.content.winfo_width() / self._get_widget_scaling()
        requested = int(self.store.settings["view_columns"])
        columns = min(requested, max(1, int(width // 310)))
        if columns != self._layout_columns:
            self._layout_columns = columns
            self._place_tiles()
            if not self.tiles:
                self.empty_frame.grid_configure(columnspan=columns)

    def _on_camera_status(self, camera_id: str, state: str, detail: str) -> None:
        if self._closed:
            return
        self._states[camera_id] = state, detail
        if self.camera_table.exists(camera_id):
            self.camera_table.set(camera_id, "state", detail)
        self._update_status()

    def _refresh_table(self) -> None:
        self._table_refreshing = True
        try:
            self.camera_table.delete(*self.camera_table.get_children())
            if self.workspace is not None:
                for camera in self.workspace.cameras:
                    detail = self._states.get(camera.id, ("disconnected", "Не подключена"))[1]
                    if not camera.enabled:
                        detail = "Отключена в настройках"
                    modules = ", ".join(label for key, label in MODULE_LABELS.items() if getattr(camera.rules, key))
                    self.camera_table.insert("", "end", iid=camera.id,
                                             values=(camera.name, detail, source_label(camera.source), modules or "Не выбраны"))
                if self.selected_camera_id and self.camera_table.exists(self.selected_camera_id):
                    self.camera_table.selection_set(self.selected_camera_id)
        finally:
            self._table_refreshing = False
        self._update_status()

    def _on_table_selection(self, _event=None) -> None:
        if not self._table_refreshing and self.camera_table.selection():
            camera_id = self.camera_table.selection()[0]
            if camera_id != self.selected_camera_id:
                self._select_camera(camera_id)

    def _select_camera(self, camera_id: str) -> None:
        self.selected_camera_id = camera_id
        if self.workspace:
            index = next(index for index, camera in enumerate(self.workspace.cameras) if camera.id == camera_id)
            if index // self.PAGE_SIZE != self._page:
                self._page = index // self.PAGE_SIZE
                self._render_wall()
            if self.camera_table.exists(camera_id):
                self.camera_table.selection_set(camera_id)
        self._highlight_selection()
        self._update_actions()
        if self.workspace:
            self.workspace_manager.refresh(self.workspace.id, camera_id)

    def _highlight_selection(self) -> None:
        for camera_id, tile in self.tiles.items():
            tile.configure(border_color=BLUE if camera_id == self.selected_camera_id else BORDER,
                            border_width=2 if camera_id == self.selected_camera_id else 1)

    def _update_actions(self) -> None:
        self.add_button.configure(state="normal" if self.workspace else "disabled")
        for button in (self.properties_button, self.modules_button):
            button.configure(state="normal" if self.selected_camera else "disabled")
        for button in (self.connect_button, self.disconnect_button):
            button.configure(state="normal" if self.tiles else "disabled")

    def _update_status(self) -> None:
        count = len(self.workspace.cameras) if self.workspace else 0
        live = sum(tile.state == "streaming" for tile in self.tiles.values())
        self.count_label.configure(text=f"Камер: {count}" if self.workspace else "")
        if hasattr(self, "status_label"):
            self.status_label.configure(text=f"{self.workspace.name if self.workspace else 'Рабочая зона не выбрана'}"
                                        f"  |  Подключено: {live} из {len(self.tiles)} на странице")

    def _update_paging(self) -> None:
        count = len(self.workspace.cameras) if self.workspace else 0
        pages = max(1, (count + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page_label.configure(text=f"Страница {self._page + 1} из {pages}" if count else "")
        self.previous_button.configure(state="normal" if self._page > 0 else "disabled")
        self.next_button.configure(state="normal" if self._page + 1 < pages else "disabled")

    def change_page(self, offset: int) -> None:
        self._page += offset
        self._render_wall()

    def connect_cameras(self) -> None:
        for tile in self.tiles.values():
            if self.selected_camera_id is None or tile.camera.id == self.selected_camera_id:
                tile.connect()

    def disconnect_cameras(self) -> None:
        for tile in self.tiles.values():
            if self.selected_camera_id is None or tile.camera.id == self.selected_camera_id:
                tile.disconnect()

    def _remember_dialog(self, dialog: ctk.CTkToplevel) -> None:
        self._dialogs = [value for value in self._dialogs if value.winfo_exists()]
        self._dialogs.append(dialog)

    def add_camera(self) -> None:
        if self.workspace is None:
            return
        workspace_id = self.workspace.id

        def add(name: str, description: str, source: int | str, transport: str, enabled: bool) -> None:
            camera = self.store.add_camera(workspace_id, name, description, source, transport, enabled)
            self.selected_camera_id = camera.id
            self._page = (len(self.workspace.cameras) - 1) // self.PAGE_SIZE
            self.workspace_manager.refresh(workspace_id, camera.id)
            self._render_wall()

        self._remember_dialog(CameraDialog(self, add))

    def edit_camera(self) -> None:
        camera = self.selected_camera
        if camera is None:
            return
        workspace_id = self.workspace.id

        def update(name: str, description: str, source: int | str, transport: str, enabled: bool) -> None:
            self.store.update_camera(workspace_id, camera.id, name=name, description=description,
                                      source=source, transport=transport, enabled=enabled)
            if camera.id in self.tiles:
                self.tiles[camera.id].set_camera(camera, self.workspace.name,
                                                auto_connect=bool(self.store.settings["auto_connect"]))
            self.workspace_manager.refresh(workspace_id, camera.id)
            self._render_wall()

        self._remember_dialog(CameraDialog(self, update, camera=camera))

    def camera_settings(self) -> None:
        camera = self.selected_camera
        if camera is None:
            return

        def save() -> None:
            self.store.save()
            self._refresh_table()

        self._remember_dialog(CameraSettingsDialog(self, camera, save))

    def application_settings(self) -> None:
        self._remember_dialog(ApplicationSettingsDialog(self, self.store, self.apply_settings))

    def apply_settings(self) -> None:
        self.winfo_toplevel().apply_theme()
        self.columns_menu.set(str(self.store.settings["view_columns"]))
        if hasattr(self, "content"):
            self._on_resize()

    def _set_columns(self, value: str) -> None:
        try:
            self.store.update_settings(view_columns=int(value))
        except (ValueError, OSError) as exc:
            messagebox.showerror("Настройки", str(exc), parent=self)
            self.columns_menu.set(str(self.store.settings["view_columns"]))
            return
        self._on_resize()

    def remove_camera(self) -> None:
        camera = self.selected_camera
        if camera is None or not messagebox.askyesno("Удаление камеры", f"Удалить камеру «{camera.name}»?", parent=self):
            return
        try:
            self.store.remove_camera(self.workspace.id, camera.id)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Удаление камеры", str(exc), parent=self)
            return
        self.selected_camera_id = None
        self.workspace_manager.refresh(self.workspace.id)
        self._render_wall()

    def remove_workspace(self) -> None:
        if self.workspace is None:
            return
        if not messagebox.askyesno("Удаление рабочей зоны",
                                  f"Удалить зону «{self.workspace.name}» и её камеры?", parent=self):
            return
        try:
            self.store.remove_workspace(self.workspace.id)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Удаление рабочей зоны", str(exc), parent=self)
            return
        self.workspace = None
        self.selected_camera_id = None
        self.workspace_manager.selected_workspace_id = None
        self.workspace_manager.selected_camera_id = None
        self.workspace_manager.refresh()
        if self.store.workspaces:
            self.workspace_manager.select_workspace(self.store.workspaces[0].id)
        else:
            self.workspace_label.configure(text="Рабочая зона не выбрана")
            self._render_wall()

    def _camera_context_menu(self, event) -> None:
        item = self.camera_table.identify_row(event.y)
        if not item:
            return
        self.camera_table.selection_set(item)
        self._select_camera(item)
        menu = tk.Menu(self, tearoff=False)
        for title, command in (("Подключить", self.connect_cameras), ("Отключить", self.disconnect_cameras),
                               ("Свойства…", self.edit_camera), ("Модули СИЗ…", self.camera_settings)):
            menu.add_command(label=title, command=command)
        menu.add_separator()
        menu.add_command(label="Удалить камеру", command=self.remove_camera)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.workspace_manager.close()
        for dialog in self._dialogs:
            if dialog.winfo_exists():
                dialog.destroy()
        tiles = list(self.tiles.values()) + self._retired_tiles
        for tile in tiles:
            tile.close(wait=False)
        deadline = time.monotonic() + 6.0
        for tile in tiles:
            tile.wait_closed(timeout=max(0.0, deadline - time.monotonic()))

    def destroy(self) -> None:
        self.close()
        super().destroy()
