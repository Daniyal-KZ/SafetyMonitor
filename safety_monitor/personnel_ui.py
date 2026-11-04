"""Worker cards without accounts, and administrator inspector accounts."""

from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image, ImageOps

from .background import ViewJobs
from .dialogs import CreationDialog
from .theme import BACKGROUND, BORDER, MUTED, RED, SURFACE, TEXT, button, entry, font


MAX_PHOTO_BYTES = 5 * 1024 * 1024


def photo_preview(data):
    """Decode on a worker; CTk images are created only by the Tk callback."""
    if not data:
        return None
    if len(data) > MAX_PHOTO_BYTES:
        raise ValueError('Фото должно занимать не больше 5 МБ.')
    try:
        with Image.open(BytesIO(data)) as source:
            if source.format not in {'JPEG', 'PNG', 'WEBP'}:
                raise ValueError('Выберите фото JPEG, PNG или WebP.')
            if source.width * source.height > 20_000_000:
                raise ValueError('Размер фото не должен превышать 20 мегапикселей.')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((180, 180), Image.Resampling.LANCZOS)
            return image.copy()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Не удалось открыть фото. Выберите исправный JPEG, PNG или WebP.') from exc


def read_photo(path):
    with Path(path).open('rb') as source:
        data = source.read(MAX_PHOTO_BYTES + 1)
    return data, photo_preview(data)


class _PersonnelDialog(CreationDialog):
    """Direct database forms have their own independent background jobs."""

    # Use the native title bar: CTk's Windows recolouring calls update() inside
    # its timer, which can reenter an asynchronous save that closes the window.
    _deactivate_windows_window_header_manipulation = True

    def __init__(self, master, title, size, on_saved):
        self._saving = False
        self._timer_ids = set()
        super().__init__(master, title, size)
        self.on_saved = on_saved
        self._jobs = ViewJobs(self)

    def after(self, ms, func=None, *args):
        if getattr(self, '_destroyed', False):
            return None
        if func is None:
            return super().after(ms)

        def invoke():
            self._timer_ids.discard(timer_id)
            if not getattr(self, '_destroyed', False):
                func(*args)

        timer_id = super().after(ms, invoke)
        self._timer_ids.add(timer_id)
        return timer_id

    def after_cancel(self, timer_id):
        self._timer_ids.discard(timer_id)
        super().after_cancel(timer_id)

    def _write(self, operation):
        if self._saving:
            return
        self._saving = True
        self.save_button.configure(state='disabled', text='Сохранение…')
        self.error_label.configure(text='')

        def saved(result):
            self._saving = False
            self.on_saved(result)
            self.destroy()

        def failed(message):
            self._saving = False
            self.save_button.configure(state='normal', text='Сохранить')
            self.show_error(ValueError(message))

        self._jobs.submit('write', operation, saved, failed, replace=False)

    def destroy(self, force=False):
        if self._saving and not force:
            return
        if hasattr(self, '_jobs'):
            self._jobs.close()
        # CustomTkinter schedules delayed titlebar/icon work on Windows.
        # A quickly saved dialog must cancel those callbacks before destruction.
        for timer_id in tuple(self._timer_ids):
            self.after_cancel(timer_id)
        super().destroy()


class WorkerDialog(_PersonnelDialog):
    def __init__(self, master, database, token, on_saved, worker=None):
        title = f"Рабочий {worker['id']:04d}" if worker else 'Добавить рабочего'
        super().__init__(master, title, '560x530', on_saved)
        self.database, self.token = database, token
        self.worker_id = worker['id'] if worker else None
        self._photo = worker.get('photo') if worker else None
        self._photo_loading = False
        self.first_name = self.add_field(0, 'Имя:')
        self.last_name = self.add_field(1, 'Фамилия:')
        self.name_entry = self.first_name
        if worker:
            self.first_name.insert(0, worker['first_name'])
            self.last_name.insert(0, worker['last_name'])
        if worker:
            ctk.CTkLabel(self.body, text=f"ID: {self.worker_id:04d}",
                         text_color=MUTED, font=font(12), anchor='w').grid(
                row=2, column=0, columnspan=2, sticky='ew', pady=(8, 4))
        self._placeholder = ctk.CTkImage(light_image=Image.new('RGB', (1, 1)), size=(1, 1))
        self._image = self._placeholder
        self.preview = ctk.CTkLabel(self.body, text='Фото не добавлено', image=self._placeholder,
                                    width=185, height=185, text_color=MUTED, fg_color=BACKGROUND)
        self.preview.grid(row=3, column=0, columnspan=2, pady=8)
        actions = ctk.CTkFrame(self.body, fg_color='transparent')
        actions.grid(row=4, column=0, columnspan=2, pady=6)
        button(actions, text='Выбрать фото…', command=self.choose_photo, width=145).grid(row=0, column=0, padx=4)
        button(actions, text='Убрать фото', command=self.remove_photo, width=120).grid(row=0, column=1, padx=4)
        if self._photo:
            self._jobs.submit('photo', lambda: photo_preview(self._photo), self._show_photo,
                              lambda message: self.show_error(ValueError(message)))

    def _show_photo(self, image):
        self._image = (ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                       if image else self._placeholder)
        self.preview.configure(image=self._image, text='' if image else 'Фото не добавлено')

    def choose_photo(self):
        if self._saving:
            return
        path = filedialog.askopenfilename(parent=self, title='Фото рабочего',
            filetypes=[('Фото', '*.jpg *.jpeg *.png *.webp')])
        if not path:
            return
        self._photo_loading = True
        self.save_button.configure(state='disabled')
        self.error_label.configure(text='Загрузка фото…')

        def loaded(value):
            self._photo, image = value
            self._photo_loading = False
            self.save_button.configure(state='normal')
            self.error_label.configure(text='')
            self._show_photo(image)

        def failed(message):
            self._photo_loading = False
            self.save_button.configure(state='normal')
            self.show_error(ValueError(message))

        self._jobs.submit('photo', lambda: read_photo(path), loaded, failed)

    def remove_photo(self):
        if self._saving:
            return
        self._jobs.cancel('photo')
        self._photo, self._photo_loading = None, False
        self.save_button.configure(state='normal')
        self.error_label.configure(text='')
        self._show_photo(None)

    def submit(self):
        if self._saving or self._photo_loading:
            return
        first, last = self.first_name.get().strip(), self.last_name.get().strip()
        if not first or not last:
            self.show_error(ValueError('Введите имя и фамилию рабочего.'))
            return
        photo = self._photo
        if self.worker_id is None:
            operation = lambda: self.database.create_worker(self.token, first, last, photo=photo)
        else:
            worker_id = self.worker_id

            def operation():
                self.database.update_worker(self.token, worker_id, first, last, photo=photo)
                return worker_id

        self._write(operation)


class InspectorDialog(_PersonnelDialog):
    def __init__(self, master, database, token, on_saved):
        super().__init__(master, 'Добавить инспектора', '550x310', on_saved)
        self.database, self.token = database, token
        self.display_name = self.add_field(0, 'Имя и фамилия:')
        self.name_entry = self.display_name
        self.username = self.add_field(1, 'Логин:', 'От 3 до 32 латинских символов')
        self.password = self.add_field(2, 'Пароль:', 'Не меньше 5 символов')
        self.password.configure(show='•')

    def submit(self):
        name, username, password = self.display_name.get().strip(), self.username.get().strip(), self.password.get()
        if not name or not username or not password:
            self.show_error(ValueError('Заполните имя, логин и пароль инспектора.'))
            return
        self._write(lambda: self.database.create_inspector(self.token, username, name, password))


class WorkersFrame(ctk.CTkFrame):
    def __init__(self, master, database, token, on_changed=None):
        super().__init__(master, fg_color=SURFACE, corner_radius=0)
        self.database, self.token, self.on_changed = database, token, on_changed
        self.can_manage = database.user(token).is_admin
        self._jobs = ViewJobs(self)
        self._rows, self._selected, self._detail = {}, None, None
        self._closed, self._loading, self._writing = False, False, False
        self._filter_after = None
        self.dialog = None
        self._select_after = None
        self._workspace_id = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        toolbar.grid(row=0, column=0, sticky='ew', padx=12, pady=12)
        toolbar.grid_columnconfigure(0, weight=1)
        self.search = entry(toolbar, placeholder_text='Поиск по имени, фамилии или ID', width=250)
        self.search.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.search.bind('<KeyRelease>', self._queue_search)
        self.search.bind('<Return>', lambda _: self.refresh())
        button(toolbar, text='Обновить', command=self.refresh, width=100).grid(row=0, column=1)
        controls = ctk.CTkFrame(toolbar, fg_color='transparent')
        controls.grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.add_button = button(controls, text='Добавить рабочего…', command=self.add_worker, width=155)
        self.add_button.grid(row=0, column=0, padx=(0, 8))
        self.edit_button = button(controls, text='Изменить…', command=self.edit_worker, width=110, state='disabled')
        self.edit_button.grid(row=0, column=1, padx=(0, 8))
        self.delete_button = button(controls, text='Удалить…', command=self.delete_worker, width=110, state='disabled')
        self.delete_button.grid(row=0, column=2)
        if not self.can_manage:
            controls.grid_remove()
            self.add_button.configure(state='disabled')
        self.scope_label = ctk.CTkLabel(toolbar, text='Все доступные рабочие зоны',
                                        font=font(12), text_color=MUTED, anchor='w')
        self.scope_label.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        self.all_workspaces_button = button(toolbar, text='Все доступные зоны',
            command=lambda: self.set_workspace(None), width=150)
        self.all_workspaces_button.grid(row=2, column=1, pady=(8, 0))
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=1, column=0, sticky='nsew', padx=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.table = ttk.Treeview(body, columns=('id', 'last', 'first', 'photo'), show='headings',
                                  selectmode='browse', style='Camera.Treeview')
        self.table.grid(row=0, column=0, sticky='nsew')
        for key, title, width in [('id', 'ID', 65), ('last', 'Фамилия', 160),
                                  ('first', 'Имя', 150), ('photo', 'Фото', 65)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=55, stretch=key in {'first', 'last'})
        scroll = ctk.CTkScrollbar(body, command=self.table.yview, width=12)
        scroll.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>', self.select_worker)
        self.table.bind('<Double-1>', lambda _: self.edit_worker())
        details = ctk.CTkFrame(body, fg_color=BACKGROUND, corner_radius=3, width=230,
                              border_color=BORDER, border_width=1)
        details.grid(row=0, column=2, sticky='ns', padx=(12, 0))
        details.grid_columnconfigure(0, weight=1)
        self._placeholder = ctk.CTkImage(light_image=Image.new('RGB', (1, 1)), size=(1, 1))
        self._image = self._placeholder
        self.preview = ctk.CTkLabel(details, text='Фото не выбрано', image=self._placeholder,
                                    width=200, height=200, text_color=MUTED)
        self.preview.grid(row=0, column=0, padx=12, pady=(12, 6))
        self.metadata = ctk.CTkLabel(details, text='Выберите рабочего.', text_color=TEXT,
                                     font=font(13), wraplength=205, justify='left')
        self.metadata.grid(row=1, column=0, sticky='ew', padx=12, pady=6)
        self.status = ctk.CTkLabel(self, text='', text_color=MUTED, font=font(12), anchor='w')
        self.status.grid(row=2, column=0, sticky='ew', padx=12, pady=10)
        self.refresh()

    @property
    def _busy(self):
        return self._jobs.any_running()

    def _fail(self, message):
        self.status.configure(text=message, text_color=RED)

    def _queue_search(self, _event=None):
        if self._filter_after:
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(250, self.refresh)

    def _clear_detail(self):
        self._jobs.cancel('snapshot')
        self._selected, self._detail = None, None
        self._image = self._placeholder
        self.preview.configure(image=self._placeholder, text='Фото не выбрано')
        self.metadata.configure(text='Выберите рабочего.')
        self._update_actions()

    def _update_actions(self):
        state = 'normal' if self.can_manage and self._detail and not self._writing else 'disabled'
        self.edit_button.configure(state=state)
        self.delete_button.configure(state=state)
        self.add_button.configure(state='normal' if self.can_manage and not self._writing else 'disabled')

    def set_workspace(self, workspace_id, name=None):
        self._workspace_id = workspace_id
        self.scope_label.configure(text=(f'Рабочая зона: {name}' if name else 'Рабочие выбранной зоны')
                                   if workspace_id else 'Все доступные рабочие зоны')
        self.refresh()

    def refresh(self, reset=False):
        if self._closed:
            return
        if self._filter_after:
            self.after_cancel(self._filter_after)
            self._filter_after = None
        previous = self._select_after or self._selected
        self._select_after = None
        self._clear_detail()
        search = self.search.get().strip()
        workspace_id = self._workspace_id
        self._loading = True
        self.status.configure(text='Загрузка рабочих…', text_color=MUTED)

        def loaded(rows):
            self._loading = False
            self._rows = {row['id']: row for row in rows}
            self.table.delete(*self.table.get_children())
            for row in rows:
                self.table.insert('', 'end', iid=str(row['id']), values=(f"{row['id']:04d}",
                    row['last_name'], row['first_name'], 'Есть' if row['has_photo'] else '—'))
            self.status.configure(text=f'Рабочих: {len(rows)}' if rows else
                                  ('Рабочих пока нет.' if self.can_manage else 'В доступных зонах пока нет рабочих.'),
                                  text_color=MUTED)
            if previous in self._rows:
                self.table.selection_set(str(previous))
                self.select_worker()

        def failed(message):
            self._loading = False
            self._rows.clear()
            self.table.delete(*self.table.get_children())
            self._fail(message)

        self._jobs.submit('list', lambda: self.database.list_workers(
            self.token, workspace_id=workspace_id, search=search), loaded, failed)

    def invalidate_access(self, *, refresh=True):
        if self._closed:
            return
        self._jobs.cancel('list')
        self._clear_detail()
        self._rows.clear()
        self.table.delete(*self.table.get_children())
        if refresh:
            self.refresh()

    def select_worker(self, _event=None):
        selection = self.table.selection()
        if not selection:
            self._clear_detail()
            return
        worker_id = int(selection[0])
        if worker_id == self._selected:
            return
        self._clear_detail()
        self._selected = worker_id
        self.metadata.configure(text=f'Рабочий {worker_id:04d}\nЗагрузка карточки…')

        def load():
            worker = self.database.get_worker(self.token, worker_id)
            return worker, photo_preview(worker.get('photo'))

        def loaded(value):
            if self._selected != worker_id:
                return
            worker, image = value
            self._detail = worker
            self._image = (ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
                           if image else self._placeholder)
            self.preview.configure(image=self._image, text='' if image else 'Фото не добавлено')
            self.metadata.configure(text=f"{worker['last_name']} {worker['first_name']}\nID: {worker_id:04d}")
            self._update_actions()

        def failed(message):
            if self._selected == worker_id:
                self._clear_detail()
                self._fail(message)

        self._jobs.submit('snapshot', load, loaded, failed)

    def _saved(self, worker_id):
        if self._closed:
            return
        self._select_after = worker_id
        if self.on_changed:
            self.on_changed()
        self.refresh()

    def add_worker(self):
        if not self.can_manage or self._writing:
            return
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.focus_set()
            return
        def created(worker_id):
            # A new card is not yet assigned; make it visible for review.
            self._workspace_id = None
            self.scope_label.configure(text='Все доступные рабочие зоны')
            self.search.delete(0, 'end')
            self._saved(worker_id)

        self.dialog = WorkerDialog(self, self.database, self.token, created)

    def edit_worker(self):
        if not self.can_manage or self._detail is None or self._writing:
            return
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.focus_set()
            return
        self.dialog = WorkerDialog(self, self.database, self.token, self._saved, worker=self._detail)

    def delete_worker(self):
        if not self.can_manage or self._detail is None or self._writing:
            return
        worker_id = self._selected
        name = f"{self._detail['last_name']} {self._detail['first_name']}"
        if not messagebox.askyesno('Удалить рабочего',
                f'Удалить карточку {name} (ID {worker_id:04d})?\nРабочий будет убран из всех рабочих зон.', parent=self):
            return
        self._writing = True
        self._update_actions()

        def saved(_):
            self._writing = False
            self._clear_detail()
            self._saved(None)

        def failed(message):
            self._writing = False
            self._update_actions()
            self._fail(message)

        self._jobs.submit('write', lambda: self.database.delete_worker(self.token, worker_id),
                          saved, failed, replace=False)

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        if self._filter_after:
            self.after_cancel(self._filter_after)
            self._filter_after = None
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy(force=True)
        self._jobs.close()
        super().destroy()


class InspectorsFrame(ctk.CTkFrame):
    def __init__(self, master, database, token, on_changed=None):
        super().__init__(master, fg_color=SURFACE, corner_radius=0)
        self.database, self.token, self.on_changed = database, token, on_changed
        self.can_manage = database.user(token).is_admin
        self._jobs, self._closed, self.dialog = ViewJobs(self), False, None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(self, fg_color='transparent')
        toolbar.grid(row=0, column=0, sticky='ew', padx=12, pady=12)
        self.add_button = button(toolbar, text='Добавить инспектора…', command=self.add_inspector,
                                 width=175, state='normal' if self.can_manage else 'disabled')
        self.add_button.grid(row=0, column=0, padx=(0, 8))
        button(toolbar, text='Обновить', command=self.refresh, width=100).grid(row=0, column=1)
        body = ctk.CTkFrame(self, fg_color='transparent')
        body.grid(row=1, column=0, sticky='nsew', padx=12)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.table = ttk.Treeview(body, columns=('name', 'username'), show='headings', style='Camera.Treeview')
        self.table.grid(row=0, column=0, sticky='nsew')
        self.table.heading('name', text='Имя и фамилия')
        self.table.heading('username', text='Логин')
        self.table.column('name', width=300)
        self.table.column('username', width=200)
        scroll = ctk.CTkScrollbar(body, command=self.table.yview, width=12)
        scroll.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scroll.set)
        self.status = ctk.CTkLabel(self, text='', font=font(12), text_color=MUTED, anchor='w')
        self.status.grid(row=2, column=0, sticky='ew', padx=12, pady=10)
        self.refresh()

    @property
    def _busy(self):
        return self._jobs.any_running()

    def refresh(self, reset=False):
        if self._closed:
            return
        if not self.can_manage:
            self.status.configure(text='Управление инспекторами доступно администратору.')
            return
        self.status.configure(text='Загрузка инспекторов…', text_color=MUTED)

        def loaded(users):
            self.table.delete(*self.table.get_children())
            for user in users:
                self.table.insert('', 'end', iid=str(user.id), values=(user.display_name, user.username))
            self.status.configure(text=f'Инспекторов: {len(users)}',
                                  text_color=MUTED)

        self._jobs.submit('list', lambda: self.database.list_inspectors(self.token), loaded,
                          lambda message: self.status.configure(text=message, text_color=RED))

    def _saved(self, _user):
        if self.on_changed:
            self.on_changed()
        self.refresh()

    def invalidate_access(self, *, refresh=True):
        if self._closed:
            return
        self._jobs.cancel('list')
        self.table.delete(*self.table.get_children())
        if refresh:
            self.refresh()

    def add_inspector(self):
        if not self.can_manage:
            return
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.focus_set()
            return
        self.dialog = InspectorDialog(self, self.database, self.token, self._saved)

    def destroy(self):
        if self._closed:
            return
        self._closed = True
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy(force=True)
        self._jobs.close()
        super().destroy()
