"""Compact native-style forms for workspaces, connections and settings."""

from __future__ import annotations

from tkinter import TclError, filedialog
from typing import Callable

import customtkinter as ctk

from .models import MODULE_LABELS, Camera, WorkspaceStore, validate_source
from .theme import (
    BACKGROUND, BLUE, BLUE_HOVER, BORDER, BUTTON, MUTED,
    RED, SURFACE, SURFACE_ALT, TEXT, font,
)


class CreationDialog(ctk.CTkToplevel):
    """Shared modal window with a conventional form and button bar."""

    def __init__(self, master: ctk.CTkBaseClass, title: str,
                 size: str = "520x300", submit_text: str = "Сохранить") -> None:
        super().__init__(master)
        self._destroyed = False
        self._activate_id: str | None = None
        self.title(title)
        self.geometry(size)
        self.resizable(False, False)
        self.configure(fg_color=BACKGROUND)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        ctk.CTkLabel(self, text=title, text_color=TEXT, font=font(17, "bold"),
                     anchor="w").grid(row=0, column=0, padx=20, pady=(16, 12), sticky="ew")
        ctk.CTkFrame(self, height=1, corner_radius=0, fg_color=BORDER).grid(
            row=1, column=0, sticky="ew")
        self.body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.body.grid(row=2, column=0, padx=20, pady=(12, 0), sticky="nsew")
        self.body.grid_columnconfigure(1, weight=1)

        self.error_label = ctk.CTkLabel(self, text="", text_color=RED, font=font(12),
                                       wraplength=int(size.split("x", 1)[0]) - 40,
                                       height=30, anchor="w", justify="left")
        self.error_label.grid(row=3, column=0, padx=20, pady=(6, 0), sticky="ew")
        footer = ctk.CTkFrame(self, fg_color=SURFACE_ALT, corner_radius=0,
                              border_width=1, border_color=BORDER)
        footer.grid(row=4, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(footer, text="Отмена", fg_color=BUTTON, hover_color=BORDER,
                      text_color=TEXT, border_color=BORDER, border_width=1,
                      height=30, width=100, corner_radius=3, font=font(),
                      command=self.destroy).grid(row=0, column=1, pady=12, padx=(8, 0))
        self.save_button = ctk.CTkButton(
            footer, text=submit_text, fg_color=BLUE, hover_color=BLUE_HOVER,
            height=30, width=110, corner_radius=3, font=font(), command=self.submit)
        self.save_button.grid(row=0, column=2, pady=12, padx=(8, 20))
        self.bind("<Escape>", lambda _event: self.destroy())
        self.bind("<Return>", lambda _event: self.submit())
        self._activate_id = self.after(50, self._activate)

    def _activate(self) -> None:
        self._activate_id = None
        if self.winfo_exists():
            self.grab_set()
            self.focus_set()
            first = getattr(self, "name_entry", None)
            if first is not None:
                first.focus_set()

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        if self._activate_id is not None:
            try:
                self.after_cancel(self._activate_id)
            except TclError:
                pass
            self._activate_id = None
        super().destroy()

    def add_field(self, row: int, label: str, placeholder: str = "") -> ctk.CTkEntry:
        ctk.CTkLabel(self.body, text=label, font=font(), text_color=TEXT, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 16), pady=6)
        entry = ctk.CTkEntry(self.body, placeholder_text=placeholder, height=30,
                             corner_radius=3, border_width=1, fg_color=SURFACE,
                             border_color=BORDER, text_color=TEXT, font=font())
        entry.grid(row=row, column=1, sticky="ew", pady=6)
        return entry

    def show_error(self, error: Exception) -> None:
        self.error_label.configure(text=str(error))

    def submit(self) -> None:
        raise NotImplementedError


class WorkspaceDialog(CreationDialog):
    def __init__(self, master: ctk.CTkBaseClass, on_submit: Callable[[str], None],
                 initial_name: str = "", title: str = "Новая рабочая зона") -> None:
        super().__init__(master, title, "500x250",
                         "Сохранить" if initial_name else "Создать")
        self.on_submit = on_submit
        self.name_entry = self.add_field(0, "Название:")
        if initial_name:
            self.name_entry.insert(0, initial_name)
        ctk.CTkLabel(self.body, text="Камеры добавляются после создания рабочей зоны.",
                     font=font(12), text_color=MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    def submit(self) -> None:
        try:
            name = self.name_entry.get().strip()
            if not name:
                raise ValueError("Укажите название рабочей зоны.")
            self.on_submit(name)
        except (ValueError, OSError) as exc:
            self.show_error(exc)
            return
        self.destroy()


class CameraDialog(CreationDialog):
    SOURCE_KINDS = ("IP-камера (RTSP)", "USB-камера", "Видеофайл")

    def __init__(self, master: ctk.CTkBaseClass,
                 on_submit: Callable[[str, str, int | str, str, bool], None],
                 camera: Camera | None = None) -> None:
        super().__init__(master, "Свойства камеры" if camera else "Добавить камеру",
                         "640x445", "Сохранить" if camera else "Добавить")
        self.on_submit = on_submit
        self.camera = camera
        self.name_entry = self.add_field(0, "Название:")
        self.description_entry = self.add_field(1, "Описание:")

        ctk.CTkLabel(self.body, text="Тип подключения:", font=font(), text_color=TEXT,
                     anchor="w").grid(row=2, column=0, sticky="w", padx=(0, 16), pady=6)
        self.source_menu = ctk.CTkOptionMenu(
            self.body, values=list(self.SOURCE_KINDS), command=self._change_source,
            height=30, corner_radius=3, font=font(), dropdown_font=font(),
            fg_color=BUTTON, button_color=BORDER, button_hover_color=BLUE,
            text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT)
        self.source_menu.grid(row=2, column=1, sticky="ew", pady=6)

        self.source_label = ctk.CTkLabel(self.body, text="Адрес RTSP:", text_color=TEXT,
                                        font=font(), anchor="w")
        self.source_label.grid(row=3, column=0, sticky="w", padx=(0, 16), pady=6)
        source_row = ctk.CTkFrame(self.body, fg_color="transparent", corner_radius=0)
        source_row.grid(row=3, column=1, sticky="ew", pady=6)
        source_row.grid_columnconfigure(0, weight=1)
        self.source_entry = ctk.CTkEntry(
            source_row, height=30, corner_radius=3, font=font(), border_width=1,
            border_color=BORDER, fg_color=SURFACE, text_color=TEXT)
        self.source_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ctk.CTkButton(
            source_row, text="…", width=34, height=30, corner_radius=3,
            fg_color=BUTTON, hover_color=BORDER, text_color=TEXT,
            border_color=BORDER, border_width=1, font=font(), command=self._browse)
        self.browse_button.grid(row=0, column=1, padx=(6, 0))

        self.hint_label = ctk.CTkLabel(self.body, text="", font=font(12), text_color=MUTED,
                                      wraplength=430, justify="left", anchor="w")
        self.hint_label.grid(row=4, column=1, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(self.body, text="Транспорт RTSP:", font=font(), text_color=TEXT,
                     anchor="w").grid(row=5, column=0, sticky="w", padx=(0, 16), pady=6)
        self.transport_menu = ctk.CTkOptionMenu(
            self.body, values=["TCP", "UDP"], height=30, corner_radius=3,
            fg_color=BUTTON, button_color=BORDER, button_hover_color=BLUE,
            text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
            font=font(), dropdown_font=font())
        self.transport_menu.grid(row=5, column=1, sticky="w", pady=6)
        self.enabled_var = ctk.BooleanVar(value=camera.enabled if camera else True)
        self.enabled_checkbox = ctk.CTkCheckBox(
            self.body, text="Камера включена", variable=self.enabled_var,
            checkbox_width=18, checkbox_height=18, corner_radius=2, border_width=1,
            border_color=BORDER, fg_color=BLUE, hover_color=BLUE_HOVER,
            text_color=TEXT, font=font())
        self.enabled_checkbox.grid(row=6, column=1, sticky="w", pady=(12, 0))

        if camera:
            self.name_entry.insert(0, camera.name)
            self.description_entry.insert(0, camera.description)
            kind = (self.SOURCE_KINDS[1] if isinstance(camera.source, int) else
                    self.SOURCE_KINDS[0] if str(camera.source).lower().startswith(("rtsp://", "rtsps://"))
                    else self.SOURCE_KINDS[2])
            self.source_menu.set(kind)
            self.transport_menu.set(camera.transport.upper())
            self._change_source(kind)
            self.source_entry.insert(0, str(camera.source))
        else:
            self._change_source(self.SOURCE_KINDS[0])

    def _change_source(self, kind: str) -> None:
        self.source_entry.delete(0, "end")
        rtsp = kind == self.SOURCE_KINDS[0]
        usb = kind == self.SOURCE_KINDS[1]
        self.source_label.configure(text="Адрес RTSP:" if rtsp else "Индекс устройства:" if usb else "Видеофайл:")
        self.source_entry.configure(placeholder_text="rtsp://…" if rtsp else "0" if usb else "Выберите файл")
        self.browse_button.configure(state="normal" if kind == self.SOURCE_KINDS[2] else "disabled")
        self.transport_menu.configure(state="normal" if rtsp else "disabled")
        self.hint_label.configure(text=(
            "Формат: rtsp://пользователь:пароль@адрес:554/путь\n"
            "Точный путь потока указан в настройках IP-камеры." if rtsp else
            "Номер USB-камеры в системе, начиная с 0." if usb else
            "Поддерживаются MP4, AVI, MOV, MKV и другие форматы OpenCV."))

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Выберите видеозапись",
            filetypes=[("Видеофайлы", "*.mp4 *.avi *.mov *.mkv *.webm *.m4v"),
                       ("Все файлы", "*.*")])
        if path:
            self.source_entry.delete(0, "end")
            self.source_entry.insert(0, path)

    def submit(self) -> None:
        try:
            name = self.name_entry.get().strip()
            if not name:
                raise ValueError("Укажите название камеры.")
            raw = self.source_entry.get().strip()
            kind = self.source_menu.get()
            if kind == self.SOURCE_KINDS[1]:
                if not raw.isdecimal():
                    raise ValueError("Введите неотрицательный индекс USB-камеры.")
                source: int | str = int(raw)
            else:
                if kind == self.SOURCE_KINDS[0] and not raw.lower().startswith(("rtsp://", "rtsps://")):
                    raise ValueError("Адрес IP-камеры должен начинаться с rtsp:// или rtsps://.")
                if kind == self.SOURCE_KINDS[2] and raw.lower().startswith(("rtsp://", "rtsps://")):
                    raise ValueError("Для сетевого адреса выберите тип «IP-камера (RTSP)».")
                source = raw
            # An existing local recording may have been moved since setup.
            # Allow its unchanged address while editing other camera fields.
            if self.camera is None or source != self.camera.source:
                source = validate_source(source)
            self.on_submit(name, self.description_entry.get().strip(), source,
                           self.transport_menu.get().lower(), bool(self.enabled_var.get()))
        except (ValueError, OSError) as exc:
            self.show_error(exc)
            return
        self.destroy()


class CameraSettingsDialog(CreationDialog):
    """Per-camera module selection, persisted only after a successful save."""

    def __init__(self, master: ctk.CTkBaseClass, camera: Camera,
                 on_save: Callable[[], None]) -> None:
        super().__init__(master, f"Настройки камеры — {camera.name}", "570x530")
        self.camera = camera
        self.on_save = on_save
        self.module_vars: dict[str, ctk.BooleanVar] = {}
        self.body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.body, text="МОДУЛИ КОНТРОЛЯ", font=font(11, "bold"),
                     text_color=MUTED, anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(2, 8))
        group = ctk.CTkFrame(self.body, fg_color=SURFACE, corner_radius=3,
                             border_color=BORDER, border_width=1)
        group.grid(row=1, column=0, columnspan=2, sticky="ew")
        group.grid_columnconfigure(0, weight=1)
        for index, (key, label) in enumerate(MODULE_LABELS.items()):
            self.module_vars[key] = ctk.BooleanVar(value=getattr(camera.rules, key))
            self._settings_row(group, index * 2, label, self.module_vars[key])
            if index < len(MODULE_LABELS) - 1:
                ctk.CTkFrame(group, height=1, corner_radius=0, fg_color=BORDER).grid(
                    row=index * 2 + 1, column=0, columnspan=2, padx=12, sticky="ew")
        ctk.CTkLabel(
            self.body,
            text="Модель анализа не подключена. Выбранные модули сохраняются\n"
                 "в настройках камеры для последующего подключения модели.",
            font=font(12), text_color=MUTED, anchor="w", justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    @staticmethod
    def _settings_row(group: ctk.CTkFrame, row: int, text: str,
                      variable: ctk.BooleanVar) -> None:
        ctk.CTkLabel(group, text=text, text_color=TEXT, font=font(), height=20, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=14, pady=8)
        ctk.CTkCheckBox(
            group, text="", width=20, height=20, variable=variable,
            checkbox_width=18, checkbox_height=18, corner_radius=2, border_width=1,
            border_color=BORDER, fg_color=BLUE, hover_color=BLUE_HOVER,
        ).grid(row=row, column=1, padx=(10, 16), pady=8)

    def submit(self) -> None:
        values = {key: bool(variable.get()) for key, variable in self.module_vars.items()}
        previous = {key: getattr(self.camera.rules, key) for key in values}
        try:
            for key, value in values.items():
                setattr(self.camera.rules, key, value)
            self.on_save()
        except (ValueError, OSError) as exc:
            for key, value in previous.items():
                setattr(self.camera.rules, key, value)
            self.show_error(exc)
            return
        self.destroy()


class ApplicationSettingsDialog(CreationDialog):
    """Application preferences presented as a compact grouped settings table."""

    THEME_VALUES = {"Светлая": "Light", "Тёмная": "Dark", "Системная": "System"}

    def __init__(self, master: ctk.CTkBaseClass, store: WorkspaceStore,
                 on_save: Callable[[], None]) -> None:
        super().__init__(master, "Настройки приложения", "570x350")
        self.store = store
        self.on_save = on_save
        group = ctk.CTkFrame(self.body, fg_color=SURFACE, corner_radius=3,
                             border_width=1, border_color=BORDER)
        group.grid(row=0, column=0, columnspan=2, sticky="ew")
        group.grid_columnconfigure(0, weight=1)
        self.theme_menu = self._option_row(group, 0, "Оформление", list(self.THEME_VALUES))
        reverse_themes = {value: label for label, value in self.THEME_VALUES.items()}
        self.theme_menu.set(reverse_themes.get(str(store.settings["theme"]), "Светлая"))
        self.columns_menu = self._option_row(group, 2, "Камер в строке", ["1", "2", "3"])
        self.columns_menu.set(str(store.settings["view_columns"]))
        self.auto_connect_var = ctk.BooleanVar(value=bool(store.settings["auto_connect"]))
        CameraSettingsDialog._settings_row(group, 4, "Подключать камеры при открытии зоны",
                                            self.auto_connect_var)
        for row in (1, 3):
            ctk.CTkFrame(group, height=1, corner_radius=0, fg_color=BORDER).grid(
                row=row, column=0, columnspan=2, sticky="ew", padx=12)
        ctk.CTkLabel(self.body, text="Параметры сохраняются между запусками приложения.",
                     font=font(12), text_color=MUTED, anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    @staticmethod
    def _option_row(group: ctk.CTkFrame, row: int, label: str,
                    values: list[str]) -> ctk.CTkOptionMenu:
        ctk.CTkLabel(group, text=label, font=font(), text_color=TEXT, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=14, pady=9)
        menu = ctk.CTkOptionMenu(
            group, values=values, width=145, height=28, corner_radius=3, font=font(),
            dropdown_font=font(), fg_color=BUTTON, button_color=BORDER,
            button_hover_color=BLUE, text_color=TEXT, dropdown_fg_color=SURFACE,
            dropdown_text_color=TEXT)
        menu.grid(row=row, column=1, sticky="e", padx=(10, 14), pady=9)
        return menu

    def submit(self) -> None:
        try:
            self.store.update_settings(
                theme=self.THEME_VALUES[self.theme_menu.get()],
                view_columns=int(self.columns_menu.get()),
                auto_connect=bool(self.auto_connect_var.get()),
            )
            self.on_save()
        except (ValueError, OSError) as exc:
            self.show_error(exc)
            return
        self.destroy()
