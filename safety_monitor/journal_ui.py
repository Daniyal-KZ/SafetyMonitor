"""Journal and snapshot library; both roles can inspect and review detections."""

from io import BytesIO
from collections import OrderedDict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

from .background import ViewJobs
from .journal_export import export_journal
from .violations import violation_label
from .theme import BACKGROUND, BORDER, SURFACE, TEXT, MUTED, RED, font, button, entry, option_menu, tabview


class JournalFrame(ctk.CTkFrame):
    def __init__(self, master, database, token):
        super().__init__(master, fg_color=SURFACE, corner_radius=0)
        self.database, self.token = database, token
        self.can_delete = database.user(token).is_admin
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._rows = {}
        self._pages = [None]
        self._filters = ('', 'all')
        self._has_next = False
        self._loading = False
        self._pending_page = None
        self._jobs = ViewJobs(self)
        self._selection_version = 0
        self._cache = OrderedDict()
        self._note_drafts = {}
        self._write_version = 0
        self._committed_notes = {}
        self._filter_after = None
        self._preview_after = None
        self._messages = {}
        self._closed = False
        self._snapshot = None
        self._selected = None
        self._placeholder = ctk.CTkImage(light_image=Image.new('RGB', (1, 1)), size=(1, 1))
        toolbar = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        toolbar.grid(row=0, column=0, sticky='ew', padx=12, pady=12)
        toolbar.grid_columnconfigure(0, weight=1)
        self.search = entry(toolbar, placeholder_text='Поиск по объекту или камере', width=200)
        self.search.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.search.bind('<Return>', lambda e: self.refresh())
        self.search.bind('<KeyRelease>', self._queue_filter)
        self.state_filter = option_menu(toolbar, values=['Все', 'Новые', 'Просмотренные'], width=145,
                                        command=lambda _: self.refresh())
        self.state_filter.grid(row=0, column=1, padx=6)
        button(toolbar, text='Обновить', command=self.refresh, width=100).grid(row=0, column=2, padx=6)
        toolbar_actions = ctk.CTkFrame(toolbar, fg_color='transparent', corner_radius=0)
        toolbar_actions.grid(row=1, column=0, columnspan=3, sticky='w', pady=(8, 0))
        self.csv_button = button(toolbar_actions, text='Экспорт CSV…', command=self.export_csv, width=130)
        self.csv_button.grid(row=0, column=0, padx=(0, 8))
        self.delete_button = button(toolbar_actions, text='Удалить выбранные…', command=self.delete_selected,
                                    width=175, state='disabled')
        self.delete_button.grid(row=0, column=1)
        if not self.can_delete:
            self.delete_button.grid_remove()
        self.tabs = tabview(self, command=self._inner_tab_changed)
        self.tabs.grid(row=1, column=0, sticky='nsew', padx=12)
        journal = self.tabs.add('Журнал')
        library = self.tabs.add('Библиотека кадров')
        journal.grid_columnconfigure(0, weight=1)
        journal.grid_rowconfigure(0, weight=1)
        columns = ('time', 'workspace', 'camera', 'kind', 'count', 'state')
        self.table = ttk.Treeview(journal, columns=columns, show='headings', style='Camera.Treeview', selectmode='extended')
        self.table.grid(row=0, column=0, sticky='nsew')
        for key, title, width in [('time', 'Время', 170), ('workspace', 'Объект', 180),
                                  ('camera', 'Камера', 150), ('kind', 'Вид нарушения', 190), ('count', 'Количество', 90),
                                  ('state', 'Статус', 160)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=70)
        scroll = ctk.CTkScrollbar(journal, command=self.table.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scroll.set)
        horizontal = ctk.CTkScrollbar(journal, orientation='horizontal', command=self.table.xview, height=12)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.table.configure(xscrollcommand=horizontal.set)
        self.table.bind('<<TreeviewSelect>>', self.select_event)
        self.table.bind('<Double-1>', self.open_frame)
        library.grid_columnconfigure(0, weight=1)
        library.grid_rowconfigure(1, weight=1)
        self.metadata = ctk.CTkLabel(library, text='Выберите событие в журнале.', text_color=TEXT, font=font(), wraplength=500)
        self.metadata.grid(row=0, column=0, sticky='ew', padx=10, pady=8)
        self.preview_area = ctk.CTkFrame(library, fg_color=BACKGROUND, corner_radius=0, border_width=1, border_color=BORDER)
        self.preview_area.grid(row=1, column=0, sticky='nsew', padx=10, pady=8)
        self.preview_area.grid_propagate(False)
        self.preview_area.grid_columnconfigure(0, weight=1)
        self.preview_area.grid_rowconfigure(0, weight=1)
        self.preview = ctk.CTkLabel(self.preview_area, text='Кадр не выбран', text_color=MUTED, font=font(12))
        self.preview.grid(row=0, column=0)
        self._preview_image = None
        self._preview_size = None
        self.preview_area.bind('<Configure>', self._queue_preview)
        actions = ctk.CTkFrame(library, fg_color='transparent', corner_radius=0)
        actions.grid(row=2, column=0, sticky='ew', padx=10, pady=10)
        actions.grid_columnconfigure(0, weight=1)
        self.note = entry(actions, placeholder_text='Комментарий инспектора (до 2000 символов)')
        self.note.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 8))
        self.save_note_button = button(actions, text='Сохранить комментарий', command=self.save_note, width=170, state='disabled')
        self.save_note_button.grid(row=1, column=0, sticky='w')
        self.review_button = button(actions, text='Отметить просмотренным', command=self.review, width=190, state='disabled')
        self.review_button.grid(row=1, column=1, padx=6)
        self.export_button = button(actions, text='Сохранить кадр…', command=self.export, width=150, state='disabled')
        self.export_button.grid(row=1, column=2)
        footer = ctk.CTkFrame(self, fg_color='transparent', corner_radius=0)
        footer.grid(row=2, column=0, sticky='ew', padx=12, pady=10)
        footer.grid_columnconfigure(0, weight=1)
        self.page_label = ctk.CTkLabel(footer, text='', font=font(12), anchor='w')
        self.page_label.grid(row=0, column=0, sticky='ew')
        self.status = ctk.CTkLabel(footer, text='', font=font(12), anchor='w', wraplength=600)
        self.status.grid(row=1, column=0, columnspan=3, sticky='ew')
        self.previous_button = button(footer, text='Назад', width=85, command=self.previous, state='disabled')
        self.previous_button.grid(row=0, column=1, padx=6)
        self.next_button = button(footer, text='Далее', width=85, command=self.next_page, state='disabled')
        self.next_button.grid(row=0, column=2)
        self.refresh()

    @property
    def _busy(self):
        # Compatibility for callers that wait for all work; actions use their own channels.
        return self._jobs.any_running()

    def _set_message(self, channel, text='', error=False):
        self._messages[channel] = (text, error)
        messages = [self._messages.get(key, ('', False)) for key in ('write', 'export', 'list', 'snapshot')]
        self.status.configure(text=' · '.join(text for text, _ in messages if text),
                              text_color=RED if any(error and text for text, error in messages) else MUTED)

    def fail(self, message):
        self._set_message('list', message, True)

    def _current_filters(self):
        return self.search.get().strip(), {'Все': 'all', 'Новые': 'new', 'Просмотренные': 'reviewed'}[self.state_filter.get()]

    def _queue_filter(self, _event=None):
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(250, self.refresh)

    def refresh(self, reset=True):
        if self._closed:
            return
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
            self._filter_after = None
        filters = self._current_filters()
        if not reset and self._pending_page is not None and filters == self._pending_page[1]:
            pages = list(self._pending_page[0])
        else:
            pages = [None] if reset or filters != self._filters else list(self._pages)
        self._load_page(pages, filters)

    def _load_page(self, pages, filters):
        self._loading = True
        self._pending_page = list(pages), filters
        self._set_message('list', 'Загрузка списка…')
        self._update_controls()
        cursor = pages[-1]
        read_version = self._write_version

        def loaded(rows):
            # Deleting the only records on the last page must return to real data.
            if not rows and len(pages) > 1:
                self._load_page(pages[:-1], filters)
                return
            self._pages, self._filters = list(pages), filters
            for row in rows:
                committed = self._committed_notes.get(row['id'])
                if committed is not None:
                    note, version = committed
                    if version > read_version:
                        row['note'] = note
                    else:
                        self._committed_notes.pop(row['id'], None)
            self._has_next = len(rows) > 100
            self._loading = False
            self._pending_page = None
            self._set_message('list')
            self.populate(rows[:100])

        def failed(message):
            self._loading = False
            self._pending_page = None
            self._set_message('list', message, True)
            self._update_controls()

        self._jobs.submit('list', lambda: self.database.list_events(self.token, *filters, cursor, limit=101), loaded, failed)

    def populate(self, rows):
        self._remember_note()
        old_selection = self.table.selection()
        self._clear_selection(remember=False)
        self.table.delete(*self.table.get_children())
        self._rows = {row['id']: row for row in rows}
        for row in rows:
            timestamp = row['occurred_at'].astimezone().strftime('%d.%m.%Y %H:%M:%S')
            self.table.insert('', 'end', iid=str(row['id']), values=(timestamp, row['workspace_name'], row['camera_name'],
                                                                   violation_label(row['kind']), row['object_count'],
                                                                   'Просмотрено' if row['reviewed_at'] else 'Новое'))
        remaining = [item for item in old_selection if int(item) in self._rows]
        if remaining:
            self.table.selection_set(remaining)
            self.select_event()
        self._update_controls()

    def _remember_note(self):
        row = self._rows.get(self._selected)
        if row is None:
            return
        text = self.note.get()
        if text != row['note']:
            self._note_drafts[self._selected] = text
            if len(self._note_drafts) > 128:
                self._note_drafts.pop(next(iter(self._note_drafts)))
        else:
            self._note_drafts.pop(self._selected, None)

    def _clear_selection(self, remember=True):
        if remember:
            self._remember_note()
        self._selection_version += 1
        self._jobs.cancel('snapshot')
        self._selected = self._snapshot = self._preview_image = self._preview_size = None
        self.preview.configure(image=self._placeholder, text='Выберите событие в журнале.')
        self.metadata.configure(text='Выберите событие в журнале.')
        self.note.delete(0, 'end')
        self._set_message('snapshot')
        self._update_controls()

    def _update_controls(self):
        writing = self._jobs.running('write')
        exporting = self._jobs.running('export')
        row = self._rows.get(self._selected)
        self.previous_button.configure(state='normal' if len(self._pages) > 1 and not self._loading else 'disabled')
        self.next_button.configure(state='normal' if self._has_next and not self._loading else 'disabled')
        self.delete_button.configure(state='normal' if self.can_delete and self.table.selection() and not writing else 'disabled')
        self.save_note_button.configure(state='normal' if row is not None and not writing else 'disabled')
        self.review_button.configure(state='normal' if row is not None and not row['reviewed_at'] and not writing else 'disabled')
        self.export_button.configure(state='normal' if self._snapshot is not None and not exporting else 'disabled')
        self.csv_button.configure(state='disabled' if exporting else 'normal')
        self.page_label.configure(text=f'Страница {len(self._pages)} · Записей: {len(self._rows)} · Время компьютера')

    def select_event(self, _event=None):
        selection = [int(value) for value in self.table.selection() if int(value) in self._rows]
        if not selection:
            self._clear_selection()
            return
        event_id = selection[0]
        if event_id == self._selected:
            self._update_controls()
            return
        self._clear_selection()
        self._selected = event_id
        row = self._rows[event_id]
        reviewer = f" · Проверил: {row['reviewer']}" if row['reviewed_at'] else ' · Новое событие'
        self.metadata.configure(text=f"#{event_id} · {row['workspace_name']} / {row['camera_name']}\n"
                                     f"Вид нарушения: {violation_label(row['kind'])} · Количество: {row['object_count']}" + reviewer)
        self.note.insert(0, self._note_drafts.get(event_id, row['note']))
        self._update_controls()
        if event_id in self._cache:
            data, prepared = self._cache[event_id]
            self._cache.move_to_end(event_id)
            self.show_snapshot(event_id, data, prepared)
            return
        self.preview.configure(text='Загрузка кадра…')
        self._set_message('snapshot', 'Загрузка кадра…')
        version = self._selection_version

        def work():
            data = self.database.event_snapshot(self.token, event_id)
            with Image.open(BytesIO(data)) as source:
                image = source.convert('RGB')
                image.thumbnail((1920, 1080))
            return data, image

        def loaded(value):
            if version != self._selection_version or self._selected != event_id or event_id not in self._rows:
                return
            data, image = value
            self._cache[event_id] = data, image
            while len(self._cache) > 4:
                self._cache.popitem(last=False)
            self.show_snapshot(event_id, data, image)

        def failed(message):
            if version == self._selection_version and self._selected == event_id:
                self.preview.configure(text='Кадр недоступен. Запись можно проверить или удалить.')
                self._set_message('snapshot', message, True)
                self._update_controls()

        self._jobs.submit('snapshot', work, loaded, failed)

    def show_snapshot(self, event_id, data, prepared=None):
        if self._selected != event_id or event_id not in self._rows:
            return
        self._snapshot = data
        self._preview_image = prepared if prepared is not None else Image.open(BytesIO(data)).convert('RGB')
        self._preview_size = None
        self._set_message('snapshot')
        self.render_preview()
        self._update_controls()

    def _inner_tab_changed(self):
        if self.tabs.get() == 'Библиотека кадров':
            self._queue_preview()

    def open_frame(self, _event=None):
        self.select_event()
        self.tabs.set('Библиотека кадров')
        self._queue_preview()

    def _queue_preview(self, _event=None):
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
        self._preview_after = self.after(60, self.render_preview)

    def render_preview(self):
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        if self._preview_image is None:
            return
        scaling = self._get_widget_scaling()
        width = max(1, int(self.preview_area.winfo_width() / scaling) - 16)
        height = max(1, int(self.preview_area.winfo_height() / scaling) - 16)
        source_width, source_height = self._preview_image.size
        factor = min(1, width / source_width, height / source_height)
        size = max(1, int(source_width * factor)), max(1, int(source_height * factor))
        if size == self._preview_size:
            return
        self._preview_size = size
        image = self._preview_image.resize(size, Image.Resampling.BILINEAR)
        self._image = ctk.CTkImage(light_image=image, dark_image=image, size=size)
        self.preview.configure(image=self._image, text='')

    def _write(self, task, completed, caption):
        if self._jobs.running('write'):
            return
        self._set_message('write', caption)

        def done(result):
            self._set_message('write')
            completed(result)
            self._update_controls()

        def failed(message):
            self._set_message('write', message, True)
            self._update_controls()

        self._jobs.submit('write', task, done, failed, replace=False)
        self._update_controls()

    def _note_saved(self, event_id, note):
        self._write_version += 1
        self._committed_notes[event_id] = note[:2000], self._write_version
        if len(self._committed_notes) > 128:
            self._committed_notes.pop(next(iter(self._committed_notes)))
        draft = self.note.get() if self._selected == event_id else self._note_drafts.get(event_id)
        if event_id in self._rows:
            self._rows[event_id]['note'] = note[:2000]
        if draft is not None and draft != note[:2000]:
            self._note_drafts[event_id] = draft
        else:
            self._note_drafts.pop(event_id, None)

    def review(self):
        if self._selected is None or self._jobs.running('write'):
            return
        event_id, note = self._selected, self.note.get()

        def saved(_):
            self._note_saved(event_id, note)
            self.refresh(reset=False)
        self._write(lambda: self.database.review_event(self.token, event_id, note), saved, 'Сохранение проверки…')

    def save_note(self):
        if self._selected is None or self._jobs.running('write'):
            return
        event_id, note = self._selected, self.note.get()

        def saved(_):
            self._note_saved(event_id, note)
            self._set_message('write', 'Комментарий сохранён.')
        self._write(lambda: self.database.update_event_note(self.token, event_id, note), saved, 'Сохранение комментария…')

    def delete_selected(self):
        if not self.can_delete or self._jobs.running('write'):
            return
        event_ids = [int(value) for value in self.table.selection() if int(value) in self._rows]
        if not event_ids:
            return
        if not messagebox.askyesno('Удаление записей',
                                   f'Удалить выбранные записи ({len(event_ids)}) вместе с кадрами?',
                                   parent=self.winfo_toplevel()):
            return

        def deleted(_):
            for event_id in event_ids:
                self._cache.pop(event_id, None)
                self._note_drafts.pop(event_id, None)
                self._committed_notes.pop(event_id, None)
            if self._selected in event_ids:
                self._clear_selection(remember=False)
            self.refresh(reset=False)
        self._write(lambda: self.database.delete_events(self.token, event_ids), deleted, 'Удаление записей…')

    def export_csv(self):
        if self._jobs.running('export'):
            return
        path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), defaultextension='.csv',
                                          initialfile='safety-journal.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        filters = self._current_filters()
        self._export(lambda: export_journal(self.database, self.token, path, *filters),
                     lambda count: f'Экспортировано записей: {count}.')

    def _export(self, task, caption):
        self._set_message('export', 'Сохранение файла…')

        def saved(result):
            self._set_message('export', caption(result))
            self._update_controls()

        def failed(message):
            self._set_message('export', message, True)
            self._update_controls()

        self._jobs.submit('export', task, saved, failed, replace=False)
        self._update_controls()

    def export(self):
        if self._snapshot is None or self._jobs.running('export'):
            return
        data, event_id = self._snapshot, self._selected
        path = filedialog.asksaveasfilename(parent=self.winfo_toplevel(), defaultextension='.jpg',
                                          initialfile=f'safety-event-{event_id}.jpg', filetypes=[('JPEG', '*.jpg')])
        if path:
            self._export(lambda: Path(path).write_bytes(data), lambda _: 'Кадр сохранён.')

    def next_page(self):
        if self._has_next and not self._loading and self._rows:
            self._load_page([*self._pages, min(self._rows)], self._filters)

    def previous(self):
        if len(self._pages) > 1 and not self._loading:
            self._load_page(self._pages[:-1], self._filters)

    def destroy(self):
        self._closed = True
        for pending in (self._filter_after, self._preview_after):
            if pending is not None:
                self.after_cancel(pending)
        self._filter_after = self._preview_after = None
        self._jobs.close()
        super().destroy()

