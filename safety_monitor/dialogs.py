"""Compact native-style forms for workspaces, connections and settings."""

from __future__ import annotations

from tkinter import TclError, filedialog, ttk
from typing import Callable

import customtkinter as ctk

from .models import MODULE_LABELS, Camera, WorkspaceStore
from .classes import RULE_CLASSES
from .inference import LEGACY_MODEL_PATH, MODEL_CHOICES, resolve_model_path
from .background import ViewJobs
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

    def save_changes(self, operation, on_saved):
        if getattr(self, '_saving', False):
            return
        self._saving = True
        caption = self.save_button.cget('text')
        self.save_button.configure(state='disabled', text='Сохранение…')
        self.error_label.configure(text='')

        def saved(result):
            on_saved(result)
            if not self._destroyed:
                self.destroy()

        def failed(error):
            if not self._destroyed:
                self._saving = False
                self.save_button.configure(state='normal', text=caption)
                self.show_error(error)

        self.master.winfo_toplevel().screen.run_store_task(operation, saved, failed)

    def submit(self) -> None:
        raise NotImplementedError


class _MemberTable(ctk.CTkFrame):
    """Persistent checkboxes in a scrollable table of workspace members."""

    def __init__(self, master, columns, variables, height):
        super().__init__(master, fg_color=SURFACE, corner_radius=0, height=1, width=1)
        self.variables = variables
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.table = ttk.Treeview(
            self, columns=('selected', *(key for key, _, _ in columns)),
            show='headings', selectmode='browse', height=height, style='Camera.Treeview')
        self.table.grid(row=0, column=0, sticky='nsew')
        self.table.heading('selected', text='Выбор', anchor='center')
        self.table.column('selected', width=66, minwidth=66, stretch=False, anchor='center')
        for key, title, width in columns:
            self.table.heading(key, text=title, anchor='w')
            self.table.column(key, width=width, minwidth=60, anchor='w', stretch=key != 'id')
        scrollbar = ctk.CTkScrollbar(self, command=self.table.yview, width=12, height=1)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind('<Button-1>', self._click)
        self.table.bind('<space>', self._toggle_focused)
        self.status = ctk.CTkLabel(self, text='Загрузка…', text_color=MUTED, font=font(12))
        self.status.grid(row=1, column=0, columnspan=2, pady=4)

    def add(self, person_id, values, selected):
        variable = ctk.BooleanVar(master=self, value=selected)
        self.variables[person_id] = variable
        row_id = str(person_id)
        self.table.insert('', 'end', iid=row_id, values=('☑' if selected else '☐', *values))
        variable.trace_add('write', lambda *_: self.table.set(
            row_id, 'selected', '☑' if variable.get() else '☐'))

    def loaded(self):
        if self.variables:
            self.status.grid_remove()
        else:
            self.status.configure(text='Пока нет записей')

    def _click(self, event):
        row_id = self.table.identify_row(event.y)
        if self.table.identify_region(event.x, event.y) == 'cell' and row_id:
            self.table.focus_set()
            self.table.focus(row_id)
            self.table.selection_set(row_id)
            return self._toggle_focused()

    def _toggle_focused(self, _event=None):
        row_id = self.table.focus()
        if row_id:
            variable = self.variables[int(row_id)]
            variable.set(not variable.get())
        return 'break'


class WorkspaceDialog(CreationDialog):
    def __init__(self, master: ctk.CTkBaseClass, on_submit: Callable,
                 initial_name: str = "", title: str = "Новая рабочая зона", workspace=None) -> None:
        super().__init__(master, title, "680x580",
                         "Сохранить" if initial_name else "Создать")
        self.on_submit = on_submit
        self.workspace = workspace
        self.inspector_vars = {}
        self.worker_vars = {}
        self._members_loaded = False
        self._jobs = ViewJobs(self)
        self.body.grid_rowconfigure(4, weight=1)
        self.name_entry = self.add_field(0, "Название:")
        if initial_name:
            self.name_entry.insert(0, initial_name)
        ctk.CTkLabel(self.body, text="Инспекторы с доступом", font=font(13, "bold"),
                     text_color=TEXT, anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 5))
        self.inspectors_list = _MemberTable(self.body,
            (('id', 'ID', 65), ('name', 'Имя и фамилия', 260), ('login', 'Логин', 190)),
            self.inspector_vars, height=3)
        self.inspectors_list.grid(row=2, column=0, columnspan=2, sticky="ew")
        ctk.CTkLabel(self.body, text="Рабочие в зоне", font=font(13, "bold"),
                     text_color=TEXT, anchor="w").grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 5))
        self.workers_list = _MemberTable(self.body,
            (('id', 'ID', 65), ('last', 'Фамилия', 225), ('first', 'Имя', 225)),
            self.worker_vars, height=4)
        self.workers_list.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self.save_button.configure(state="disabled")
        app = self.master.winfo_toplevel()
        database, token = app.database, app.token

        def load():
            return database.list_inspectors(token), database.list_workers(token)

        self._jobs.submit('members', load, self._loaded, self._load_failed)

    def _loaded(self, result):
        inspectors, workers = result
        selected_inspectors = set(self.workspace.inspector_ids) if self.workspace else set()
        selected_workers = set(self.workspace.worker_ids) if self.workspace else set()
        for person in inspectors:
            self.inspectors_list.add(person.id, (person.id, person.display_name, person.username),
                                     person.id in selected_inspectors)
        for person in workers:
            self.workers_list.add(person['id'],
                (f"{person['id']:04d}", person['last_name'], person['first_name']),
                person['id'] in selected_workers)
        self.inspectors_list.loaded()
        self.workers_list.loaded()
        self._members_loaded = True
        self.save_button.configure(state='normal')

    def _load_failed(self, message):
        self.show_error(ValueError(message + " Закройте окно и попробуйте снова."))

    def submit(self) -> None:
        if getattr(self, '_saving', False) or not self._members_loaded:
            return
        try:
            name = self.name_entry.get().strip()
            if not name:
                raise ValueError("Укажите название рабочей зоны.")
            inspectors = [key for key, variable in self.inspector_vars.items() if variable.get()]
            workers = [key for key, variable in self.worker_vars.items() if variable.get()]
            self.save_changes(*self.on_submit(name, inspectors, workers))
        except (ValueError, OSError) as exc:
            self.show_error(exc)
            return


class CameraDialog(CreationDialog):
    SOURCE_KINDS = ("IP-камера (RTSP)", "USB-камера", "Видеофайл")

    def __init__(self, master: ctk.CTkBaseClass,
                 on_submit: Callable[[str, str, int | str, str, bool], tuple],
                 camera: Camera | None = None) -> None:
        super().__init__(master, "Свойства камеры" if camera else "Добавить камеру",
                         "640x475", "Сохранить" if camera else "Добавить")
        self.on_submit = on_submit
        self.camera = camera
        self.id_entry = self.add_field(0, "ID:")
        self.id_entry.insert(0, f'{camera.display_id:04d}' if camera and camera.display_id else '—')
        self.id_entry.configure(state='readonly')
        self.name_entry = self.add_field(1, "Название:")
        self.description_entry = self.add_field(2, "Описание:")

        ctk.CTkLabel(self.body, text="Тип подключения:", font=font(), text_color=TEXT,
                     anchor="w").grid(row=3, column=0, sticky="w", padx=(0, 16), pady=6)
        self.source_menu = ctk.CTkOptionMenu(
            self.body, values=list(self.SOURCE_KINDS), command=self._change_source,
            height=30, corner_radius=3, font=font(), dropdown_font=font(),
            fg_color=BUTTON, button_color=BORDER, button_hover_color=BLUE,
            text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT)
        self.source_menu.grid(row=3, column=1, sticky="ew", pady=6)

        self.source_label = ctk.CTkLabel(self.body, text="Адрес RTSP:", text_color=TEXT,
                                        font=font(), anchor="w")
        self.source_label.grid(row=4, column=0, sticky="w", padx=(0, 16), pady=6)
        source_row = ctk.CTkFrame(self.body, fg_color="transparent", corner_radius=0)
        source_row.grid(row=4, column=1, sticky="ew", pady=6)
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
        self.hint_label.grid(row=5, column=1, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(self.body, text="Транспорт RTSP:", font=font(), text_color=TEXT,
                     anchor="w").grid(row=6, column=0, sticky="w", padx=(0, 16), pady=6)
        self.transport_menu = ctk.CTkOptionMenu(
            self.body, values=["TCP", "UDP"], height=30, corner_radius=3,
            fg_color=BUTTON, button_color=BORDER, button_hover_color=BLUE,
            text_color=TEXT, dropdown_fg_color=SURFACE, dropdown_text_color=TEXT,
            font=font(), dropdown_font=font())
        self.transport_menu.grid(row=6, column=1, sticky="w", pady=6)
        self.enabled_var = ctk.BooleanVar(value=camera.enabled if camera else True)
        self.enabled_checkbox = ctk.CTkCheckBox(
            self.body, text="Камера включена", variable=self.enabled_var,
            checkbox_width=18, checkbox_height=18, corner_radius=2, border_width=1,
            border_color=BORDER, fg_color=BLUE, hover_color=BLUE_HOVER,
            text_color=TEXT, font=font())
        self.enabled_checkbox.grid(row=7, column=1, sticky="w", pady=(12, 0))

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
            "rtsp://пользователь:пароль@адрес:554/путь" if rtsp else
            "Индекс: 0, 1, 2…" if usb else "MP4, AVI, MOV, MKV"))

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Выберите видеозапись",
            filetypes=[("Видеофайлы", "*.mp4 *.avi *.mov *.mkv *.webm *.m4v"),
                       ("Все файлы", "*.*")])
        if path:
            self.source_entry.delete(0, "end")
            self.source_entry.insert(0, path)

    def submit(self) -> None:
        if getattr(self, '_saving', False):
            return
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
            # Filesystem checks and persistence run in the store worker.
            self.save_changes(*self.on_submit(name, self.description_entry.get().strip(), source,
                              self.transport_menu.get().lower(), bool(self.enabled_var.get())))
        except (ValueError, OSError) as exc:
            self.show_error(exc)
            return


class CameraSettingsDialog(CreationDialog):
    """Per-camera module selection, persisted only after a successful save."""

    def __init__(self, master: ctk.CTkBaseClass, camera: Camera,
                 on_save: Callable[[dict[str, bool]], tuple], model_path: str = "best.pt") -> None:
        super().__init__(master, f"Настройки камеры — {camera.name}", "570x475")
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
        legacy = resolve_model_path(model_path) == LEGACY_MODEL_PATH.resolve()
        supported = {"helmet"} if legacy else set(RULE_CLASSES)
        for index, (key, label) in enumerate(MODULE_LABELS.items()):
            self.module_vars[key] = ctk.BooleanVar(value=getattr(camera.rules, key))
            self._settings_row(group, index * 2,
                               label if key in supported else label + " (нет в модели)",
                               self.module_vars[key], supported=key in supported)
            if index < len(MODULE_LABELS) - 1:
                ctk.CTkFrame(group, height=1, corner_radius=0, fg_color=BORDER).grid(
                    row=index * 2 + 1, column=0, columnspan=2, padx=12, sticky="ew")

    @staticmethod
    def _settings_row(group: ctk.CTkFrame, row: int, text: str,
                      variable: ctk.BooleanVar, supported: bool = True) -> None:
        ctk.CTkLabel(group, text=text, text_color=TEXT, font=font(), height=20, anchor="w").grid(
            row=row, column=0, sticky="ew", padx=14, pady=8)
        ctk.CTkCheckBox(
            group, text="", width=20, height=20, variable=variable,
            state="normal" if supported else "disabled",
            checkbox_width=18, checkbox_height=18, corner_radius=2, border_width=1,
            border_color=BORDER, fg_color=BLUE, hover_color=BLUE_HOVER,
        ).grid(row=row, column=1, padx=(10, 16), pady=8)

    def submit(self) -> None:
        values = {key: bool(variable.get()) for key, variable in self.module_vars.items()}
        self.save_changes(*self.on_save(values))


class ApplicationSettingsDialog(CreationDialog):
    """Application preferences presented as a compact grouped settings table."""

    THEME_VALUES = {"Светлая": "Light", "Тёмная": "Dark", "Системная": "System"}

    def __init__(self, master: ctk.CTkBaseClass, store: WorkspaceStore,
                 on_save: Callable[[], None]) -> None:
        super().__init__(master, "Настройки приложения", "650x480" if store.can_manage else "610x350")
        self.store = store
        self.can_manage = store.can_manage
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
        retry_values = sorted({1, 3, 5, 10, 15, 30, 60, 120, 300, int(store.settings['reconnect_seconds'])})
        self.reconnect_menu = self._option_row(group, 6, "Повтор подключения, секунд", [str(v) for v in retry_values])
        self.reconnect_menu.set(str(store.settings['reconnect_seconds']))
        for row in (1, 3, 5):
            ctk.CTkFrame(group, height=1, corner_radius=0, fg_color=BORDER).grid(
                row=row, column=0, columnspan=2, sticky="ew", padx=12)
        if not self.can_manage:
            return
        self.model_paths = {label: path for path, label in MODEL_CHOICES.items()}
        current_path = str(store.settings["model_path"])
        if current_path not in self.model_paths.values():
            self.model_paths[f"RF-DETR: {resolve_model_path(current_path).name}"] = current_path
        self.model_menu = self._option_row(group, 8, "Общая модель распознавания", list(self.model_paths))
        self.model_menu.configure(width=255)
        self.model_menu.set(next(label for label, path in self.model_paths.items() if path == current_path))
        self.model_menu.configure(command=lambda _: self._show_model_path())
        ctk.CTkButton(group, text="Выбрать файл RF-DETR…", command=self._choose_model,
                      width=210, height=28, corner_radius=3).grid(
            row=9, column=1, sticky="e", padx=14, pady=(0, 9))
        self.model_path_label = ctk.CTkLabel(
            self.body, text="", font=font(11), text_color=MUTED,
            anchor="w", justify="left", wraplength=590)
        self.model_path_label.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self._show_model_path()
        for row in (7,):
            ctk.CTkFrame(group, height=1, corner_radius=0, fg_color=BORDER).grid(
                row=row, column=0, columnspan=2, sticky="ew", padx=12)

    def _show_model_path(self) -> None:
        path = resolve_model_path(self.model_paths[self.model_menu.get()])
        self.model_path_label.configure(text=str(path) + ("" if path.is_file() else "\nФайл пока отсутствует."))

    def _choose_model(self) -> None:
        if not self.can_manage:
            return
        path = filedialog.askopenfilename(parent=self, title="Модель RF-DETR",
                                          filetypes=[("Веса RF-DETR", "*.pt")])
        if path:
            resolved = resolve_model_path(path)
            label = next((label for label, saved in self.model_paths.items()
                          if resolve_model_path(saved) == resolved), None)
            if label is None:
                base = f"RF-DETR: {resolved.name}"
                label, number = base, 2
                while label in self.model_paths:
                    label = f"{base} ({number})"
                    number += 1
                self.model_paths[label] = path
            self.model_menu.configure(values=list(self.model_paths))
            self.model_menu.set(label)
            self._show_model_path()

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
        model_path = self.model_paths[self.model_menu.get()] if self.can_manage else self.store.settings['model_path']
        if self.can_manage and model_path != self.store.settings["model_path"]:
            path = resolve_model_path(model_path)
            if not path.is_file():
                self.show_error(ValueError(f"Файл модели не найден: {path}"))
                return
            if path != LEGACY_MODEL_PATH.resolve() and not path.with_suffix('.labels.json').is_file():
                self.show_error(ValueError("Рядом с весами RF-DETR нужна карта .labels.json."))
                return
        settings = dict(theme=self.THEME_VALUES[self.theme_menu.get()],
                        view_columns=int(self.columns_menu.get()),
                        auto_connect=bool(self.auto_connect_var.get()),
                        reconnect_seconds=int(self.reconnect_menu.get()))
        if self.can_manage and model_path != self.store.settings['model_path']:
            settings['model_path'] = model_path
        self.save_changes(lambda store: store.update_settings(**settings), lambda _: self.on_save())
