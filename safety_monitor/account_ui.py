"""Login, profile and administrator account forms."""

from concurrent.futures import ThreadPoolExecutor
import tkinter as tk

import customtkinter as ctk
import psycopg

from .database import DatabaseWorkspaceStore
from .theme import BACKGROUND, BORDER, SURFACE, TEXT, MUTED, RED, font, button, entry


def error_text(exc):
    if isinstance(exc, psycopg.Error):
        return 'Ошибка PostgreSQL. Проверьте подключение к базе.'
    return str(exc)


class AsyncMixin:
    """Run I/O off Tk; poll completion only from the main thread."""

    def start_task(self, task, success, failure):
        if getattr(self, '_busy', False):
            return
        self._busy = True
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(task)
        executor.shutdown(wait=False)
        poll_id = None

        def destroyed(event):
            if event.widget is self:
                if poll_id is not None:
                    self.after_cancel(poll_id)
                future.cancel()

        destroy_binding = tk.Misc.bind(self, '<Destroy>', destroyed, add='+')

        def poll():
            nonlocal poll_id
            poll_id = None
            if not self.winfo_exists():
                return
            if not future.done():
                poll_id = self.after(50, poll)
                return
            tk.Misc.unbind(self, '<Destroy>', destroy_binding)
            self._busy = False
            try:
                value = future.result()
            except Exception as exc:
                failure(error_text(exc))
            else:
                success(value)

        poll_id = self.after(50, poll)


class LoginFrame(ctk.CTkFrame, AsyncMixin):
    def __init__(self, master, database, on_login):
        super().__init__(master, fg_color=BACKGROUND, corner_radius=0)
        self.database, self.on_login = database, on_login
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure((0, 2), weight=1)
        form = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, border_width=1, border_color=BORDER)
        form.grid(row=1, column=0, padx=30, pady=30)
        ctk.CTkLabel(form, text='Safety Monitor', font=font(24, 'bold'), text_color=TEXT).pack(padx=40, pady=(30, 8))
        ctk.CTkLabel(form, text='Вход в систему наблюдения', font=font(), text_color=MUTED).pack(pady=(0, 20))
        self.username = entry(form, placeholder_text='Логин', width=300)
        self.username.pack(padx=30, pady=6)
        self.password = entry(form, placeholder_text='Пароль', show='•', width=300)
        self.password.pack(padx=30, pady=6)
        self.error = ctk.CTkLabel(form, text='', text_color=RED, wraplength=330, font=font(12))
        self.error.pack(padx=20, pady=8)
        self.button = button(form, text='Войти', command=self.submit, width=300)
        self.button.pack(padx=30, pady=(0, 30))
        self.username.bind('<Return>', lambda e: self.submit())
        self.password.bind('<Return>', lambda e: self.submit())
        self.username.focus_set()

    def submit(self):
        if getattr(self, '_busy', False):
            return
        username, password = self.username.get(), self.password.get()
        self.button.configure(state='disabled', text='Подключение…')
        self.error.configure(text='')

        def authenticate():
            self.database.initialize()
            token = self.database.login(username, password)
            try:
                return token, DatabaseWorkspaceStore(self.database, token)
            except Exception:
                self.database.logout(token)
                raise

        def failed(message):
            self.error.configure(text=message)
            self.button.configure(state='normal', text='Войти')

        self.start_task(authenticate, self.on_login, failed)
