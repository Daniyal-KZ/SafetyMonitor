"""Authenticated desktop application with role-aware monitoring and journal."""

import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from .account_ui import LoginFrame, error_text
from .database import Database
from .dashboard import MainDashboard
from .events import EventRecorder
from .journal_ui import JournalFrame
from .models import DEFAULT_SETTINGS
from .theme import BACKGROUND, BORDER, MUTED, SURFACE, TEXT


class App(ctk.CTk):
    def __init__(self, database: Database | None = None) -> None:
        ctk.set_appearance_mode('Light')
        ctk.set_default_color_theme('blue')
        super().__init__()
        self.database = database if database is not None else Database()
        self.store = None
        self.token = None
        self.screen = None
        self.recorder = None
        self._sections = {}
        self._closed = False
        self._journal_after = None
        self.title('Safety Monitor — вход')
        scale = self._get_window_scaling()
        width = max(900, min(1280, int(self.winfo_screenwidth() / scale) - 80))
        height = max(520, min(800, int(self.winfo_screenheight() / scale) - 100))
        self.geometry(f'{width}x{height}')
        self.minsize(900, 520)
        self.configure(fg_color=BACKGROUND)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.apply_theme()
        self.show_login()
        self.bind('<Control-n>', lambda _event: self.screen.workspace_manager.create_workspace()
                  if self.screen is not None and self.store.can_manage else None)
        self.bind('<Control-o>', lambda _event: self.screen.workspace_manager.open_workspace() if self.screen else None)

    def show_login(self):
        self.configure(menu=tk.Menu(self))
        self.login_frame = LoginFrame(self, self.database, self.start_session)
        self.login_frame.grid(row=0, column=0, sticky='nsew')

    def start_session(self, session):
        self.token, self.store = session
        self.login_frame.destroy()
        user = self.database.user(self.token)
        role = 'Администратор' if user.is_admin else 'Инспектор'
        self.title(f'Safety Monitor — {user.display_name} · {role}')
        self.recorder = EventRecorder(self.database, self.token)
        self.screen = MainDashboard(self, self.store)
        self.screen.grid(row=0, column=0, sticky='nsew')
        for name in ('Журнал',):
            tab = self.screen.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.screen.tabs.configure(command=self._section_changed)
        self._build_menu()
        self._poll_journal()

    def _poll_journal(self):
        if self.screen is None or self._closed:
            return
        detail = self.recorder.last_error if self.recorder else ''
        self.screen.journal_label.configure(text=detail or 'Журнал: запись включена',
                                            text_color=('#AA3434', '#E28585') if detail else MUTED)
        self._journal_after = self.after(1000, self._poll_journal)

    def open_journal(self):
        self.show_section('Журнал')

    def show_section(self, name):
        if not self.token:
            return
        self.screen.tabs.set(name)
        self._section_changed()

    def _section_changed(self):
        name = self.screen.tabs.get()
        sections = {'Журнал': JournalFrame}
        if name in sections and name not in self._sections:
            frame = sections[name](self.screen.tabs.tab(name), self.database, self.token)
            frame.grid(row=0, column=0, sticky='nsew')
            self._sections[name] = frame
        elif name in sections:
            self._sections[name].refresh(reset=False)
        if name in {'Наблюдение', 'Камеры'}:
            self.screen.paging.grid()
            if name == 'Камеры':
                self.screen._refresh_table()
        else:
            self.screen.paging.grid_remove()

    def _end_session(self):
        if self._journal_after:
            self.after_cancel(self._journal_after)
            self._journal_after = None
        self._sections.clear()
        if self.screen is not None:
            self.screen.destroy()
            self.screen = None
        if self.recorder is not None:
            self.recorder.close(wait=False)
            self.recorder = None
        if self.token:
            self.database.logout(self.token)
            self.token = None

    def logout(self):
        self._end_session()
        self.store = None
        self.title('Safety Monitor — вход')
        self.show_login()

    def apply_theme(self):
        ctk.set_appearance_mode((self.store.settings if self.store is not None else DEFAULT_SETTINGS)['theme'])
        index = 1 if ctk.get_appearance_mode() == 'Dark' else 0
        for name in ('Navigation.Treeview', 'Camera.Treeview'):
            self.style.configure(name, background=SURFACE[index], fieldbackground=SURFACE[index],
                                 foreground=TEXT[index], bordercolor=BORDER[index],
                                 lightcolor=BORDER[index], darkcolor=BORDER[index], rowheight=28,
                                 font=('Segoe UI', 10), borderwidth=0)
            self.style.map(name, background=[('selected', '#D5E4F4' if index == 0 else '#354C66')],
                           foreground=[('selected', TEXT[index])])
        self.style.configure('Camera.Treeview.Heading', background=BACKGROUND[index], foreground=TEXT[index],
                             font=('Segoe UI', 10, 'bold'), padding=(8, 6))

    def _build_menu(self):
        admin_state = 'normal' if self.store.can_manage else 'disabled'
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label='Создать объект / рабочую зону…', accelerator='Ctrl+N',
                              command=self.screen.workspace_manager.create_workspace, state=admin_state)
        file_menu.add_command(label='Открыть рабочую зону…', accelerator='Ctrl+O', command=self.screen.workspace_manager.open_workspace)
        file_menu.add_command(label='Переименовать рабочую зону…', command=self.screen.workspace_manager.rename_workspace, state=admin_state)
        file_menu.add_command(label='Удалить рабочую зону…', command=self.screen.remove_workspace, state=admin_state)
        file_menu.add_separator()
        file_menu.add_command(label='Выйти из аккаунта', command=self.logout)
        file_menu.add_command(label='Выход из программы', command=self.close)
        menu.add_cascade(label='Файл', menu=file_menu)
        cameras = tk.Menu(menu, tearoff=False)
        for title, command in [('Добавить камеру…', self.screen.add_camera), ('Свойства камеры…', self.screen.edit_camera),
                               ('Модули СИЗ…', self.screen.camera_settings), ('Удалить камеру…', self.screen.remove_camera)]:
            cameras.add_command(label=title, command=command, state=admin_state)
        cameras.add_separator()
        cameras.add_command(label='Подключить', command=self.screen.connect_cameras)
        cameras.add_command(label='Отключить', command=self.screen.disconnect_cameras)
        menu.add_cascade(label='Камеры', menu=cameras)
        view = tk.Menu(menu, tearoff=False)
        view.add_command(label='Наблюдение', command=lambda: self.show_section('Наблюдение'))
        view.add_command(label='Список камер', command=lambda: self.show_section('Камеры'))
        view.add_command(label='Журнал и библиотека кадров', command=self.open_journal)
        menu.add_cascade(label='Вид', menu=view)
        settings = tk.Menu(menu, tearoff=False)
        settings.add_command(label='Настройки приложения…', command=self.screen.application_settings, state=admin_state)
        menu.add_cascade(label='Настройки', menu=settings)
        self.configure(menu=menu)

    def report_callback_exception(self, exc_type, exc, traceback):
        messagebox.showerror('Safety Monitor', error_text(exc), parent=self)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._end_session()
        super().destroy()

    def destroy(self):
        self.close()
