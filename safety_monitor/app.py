"""Local application window with a traditional native menu bar."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from .dashboard import MainDashboard
from .models import WorkspaceStore
from .theme import BACKGROUND, BORDER, SURFACE, TEXT


class App(ctk.CTk):
    def __init__(self, config_path: Path | str | None = None) -> None:
        store = WorkspaceStore(config_path if config_path is not None
                               else Path(__file__).resolve().parent.parent / "data" / "config.json")
        ctk.set_appearance_mode(store.settings["theme"])
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.store = store
        self._closed = False
        self.title("Safety Monitor")
        scale = self._get_window_scaling()
        width = max(900, min(1280, int(self.winfo_screenwidth() / scale) - 80))
        height = max(520, min(800, int(self.winfo_screenheight() / scale) - 100))
        self.geometry(f"{width}x{height}")
        self.minsize(900, 520)
        self.configure(fg_color=BACKGROUND)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self.apply_theme()
        self.screen = MainDashboard(self, self.store)
        self.screen.grid(row=0, column=0, sticky="nsew")
        self._build_menu()
        self.bind("<Control-n>", lambda _event: self.screen.workspace_manager.create_workspace())
        self.bind("<Control-o>", lambda _event: self.screen.workspace_manager.open_workspace())

    def apply_theme(self) -> None:
        ctk.set_appearance_mode(self.store.settings["theme"])
        index = 1 if ctk.get_appearance_mode() == "Dark" else 0
        for name in ("Navigation.Treeview", "Camera.Treeview"):
            self.style.configure(name, background=SURFACE[index], fieldbackground=SURFACE[index],
                                  foreground=TEXT[index], bordercolor=BORDER[index],
                                  lightcolor=BORDER[index], darkcolor=BORDER[index],
                                  rowheight=28, font=("Segoe UI", 10), borderwidth=0)
            self.style.map(name, background=[("selected", "#D5E4F4" if index == 0 else "#354C66")],
                            foreground=[("selected", TEXT[index])])
        self.style.configure("Camera.Treeview.Heading", background=BACKGROUND[index], foreground=TEXT[index],
                              bordercolor=BORDER[index], font=("Segoe UI", 10, "bold"), padding=(8, 6))

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Создать рабочую зону…", accelerator="Ctrl+N",
                              command=self.screen.workspace_manager.create_workspace)
        file_menu.add_command(label="Открыть рабочую зону…", accelerator="Ctrl+O",
                              command=self.screen.workspace_manager.open_workspace)
        file_menu.add_command(label="Переименовать рабочую зону…", command=self.screen.workspace_manager.rename_workspace)
        file_menu.add_command(label="Удалить рабочую зону…", command=self.screen.remove_workspace)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.close)
        menu.add_cascade(label="Файл", menu=file_menu)
        camera_menu = tk.Menu(menu, tearoff=False)
        for title, command in (("Добавить камеру…", self.screen.add_camera),
                                ("Свойства камеры…", self.screen.edit_camera),
                                ("Модули СИЗ…", self.screen.camera_settings),
                                ("Удалить камеру…", self.screen.remove_camera)):
            camera_menu.add_command(label=title, command=command)
        camera_menu.add_separator()
        camera_menu.add_command(label="Подключить", command=self.screen.connect_cameras)
        camera_menu.add_command(label="Отключить", command=self.screen.disconnect_cameras)
        menu.add_cascade(label="Камеры", menu=camera_menu)
        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_command(label="Наблюдение", command=lambda: self.screen.tabs.set("Наблюдение"))
        view_menu.add_command(label="Список камер", command=lambda: self.screen.tabs.set("Камеры"))
        view_menu.add_command(label="Журнал событий", command=lambda: self.screen.tabs.set("Журнал событий"))
        menu.add_cascade(label="Вид", menu=view_menu)
        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(label="Настройки приложения…", command=self.screen.application_settings)
        settings_menu.add_command(label="Выбрать модель…", command=self.screen.select_model)
        settings_menu.add_command(label="Модули выбранной камеры…", command=self.screen.camera_settings)
        menu.add_cascade(label="Настройки", menu=settings_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="О программе", command=lambda: messagebox.showinfo(
            "Safety Monitor", "Safety Monitor\n\nРабочие зоны и видеопотоки IP-камер.\n"
            "Пять классов: PERSON, HELMET, VEST, GLOVES, PPE GLASSES.\n"
            "Журнал событий с фото, временем и камерой.\n"
            "События отсутствия СИЗ требуют проверки оператором.", parent=self))
        menu.add_cascade(label="Справка", menu=help_menu)
        self.configure(menu=menu)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if hasattr(self, "screen"):
            self.screen.close()
        super().destroy()

    def destroy(self) -> None:
        self.close()
