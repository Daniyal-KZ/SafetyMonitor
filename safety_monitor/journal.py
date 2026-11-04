"""Event journal with bounded pages, evidence preview and CSV export."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

from .classes import RULE_LABELS
from .theme import BACKGROUND, BORDER, MUTED, SURFACE, TEXT, font


class EventJournal(ctk.CTkFrame):
    PAGE_SIZE = 100

    def __init__(self, master, events, analysis, select_model):
        super().__init__(master, fg_color=SURFACE, corner_radius=0)
        self.events, self.analysis = events, analysis
        self._page = 0
        self._rows = {}
        self._checked = set()
        self._signature = None
        self._after_id = None
        self._previews = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        toolbar.grid_columnconfigure(0, weight=1)
        self.search = ctk.CTkEntry(toolbar, placeholder_text="Камера, зона или дата ГГГГ-ММ-ДД", font=font(12))
        self.search.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.search.bind("<Return>", lambda _event: self.apply_filter())
        self.rule = ctk.CTkOptionMenu(toolbar, values=["Все СИЗ", *RULE_LABELS.values()], width=155,
                                     command=lambda _: self.apply_filter(), font=font(12))
        self.rule.grid(row=0, column=1, padx=3)
        self._button(toolbar, "Найти", self.apply_filter, 65).grid(row=0, column=2, padx=3)
        self._button(toolbar, "CSV", self.export, 55).grid(row=0, column=3, padx=3)
        self._button(toolbar, "Выбрать модель…", select_model, 180).grid(row=0, column=4, padx=(3, 0))
        self.note = ctk.CTkLabel(self, text="Нарушение 30 секунд → событие со снимком. Двойной щелчок — фото и подробности.",
                                anchor="w", font=font(12), text_color=MUTED)
        self.note.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 5))
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=2, column=0, sticky="nsew", padx=8)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        self.table = ttk.Treeview(frame, columns=("check", "time", "camera", "zone", "missing", "track"),
                                 show="headings", selectmode="browse", style="Camera.Treeview")
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.heading("check", text="☐", command=self.toggle_all)
        self.table.column("check", width=42, minwidth=42, stretch=False, anchor="center")
        for name, title, width in (("time", "Время (UTC+05:00)", 185), ("camera", "Камера", 140),
                                    ("zone", "Рабочая зона", 135), ("missing", "Не обнаружено · проверить", 280),
                                    ("track", "Трек", 65)):
            self.table.heading(name, text=title, anchor="w")
            self.table.column(name, width=width, minwidth=60, anchor="w")
        scrollbar = ctk.CTkScrollbar(frame, command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal = ctk.CTkScrollbar(frame, command=self.table.xview, orientation="horizontal")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.table.bind("<Button-1>", self.toggle_click)
        self.table.bind("<space>", self.toggle_focused)
        self.table.bind("<Double-1>", self.double_click)
        self.table.bind("<Return>", lambda _: self.preview())
        footer = ctk.CTkFrame(self, fg_color=BACKGROUND, corner_radius=0)
        footer.grid(row=3, column=0, sticky="ew", padx=8, pady=8)
        footer.grid_columnconfigure(0, weight=1)
        self.count = ctk.CTkLabel(footer, text="", font=font(12), text_color=MUTED, anchor="w")
        self.count.grid(row=0, column=0, sticky="ew", padx=4)
        self._button(footer, "Открыть фото", self.preview, 110).grid(row=0, column=1, padx=3)
        self.previous = self._button(footer, "Назад", lambda: self.change_page(-1), 70)
        self.previous.grid(row=0, column=2, padx=3)
        self.next = self._button(footer, "Далее", lambda: self.change_page(1), 70)
        self.next.grid(row=0, column=3, padx=3)
        self.delete_button = self._button(footer, "Удалить выбранные (0)", self.delete_selected, 190)
        self.delete_button.grid(row=0, column=4, padx=3)
        self.delete_button.configure(state="disabled")
        self.refresh()
        self._schedule()

    @staticmethod
    def _button(master, text, command, width):
        return ctk.CTkButton(master, text=text, command=command, width=width, height=28,
                             fg_color=SURFACE, text_color=TEXT, border_width=1,
                             border_color=BORDER, hover_color=BORDER, corner_radius=3, font=font(12))

    def filters(self):
        rule = next((key for key, label in RULE_LABELS.items() if label == self.rule.get()), "")
        return self.search.get().strip(), rule

    def apply_filter(self):
        self._checked.clear()
        self._page = 0
        self._signature = None
        self.refresh()

    def change_page(self, delta):
        self._checked.clear()
        self._page = max(0, self._page + delta)
        self.refresh()

    def refresh(self):
        try:
            rows, total = self.events.query(*self.filters(), limit=self.PAGE_SIZE, offset=self._page*self.PAGE_SIZE)
            last_page = max(0, (total - 1) // self.PAGE_SIZE)
            if self._page > last_page:
                self._page = last_page
                rows, total = self.events.query(*self.filters(), limit=self.PAGE_SIZE, offset=self._page*self.PAGE_SIZE)
            signature = tuple(row["id"] for row in rows)
            if signature != self._signature:
                selected = self.table.selection()
                children = self.table.get_children()
                if children:
                    self.table.delete(*children)
                self._rows = {row["id"]: row for row in rows}
                self._checked.intersection_update(self._rows)
                for row in rows:
                    missing = ", ".join(RULE_LABELS[key] for key in json.loads(row["missing"]))
                    self.table.insert("", "end", iid=row["id"], values=("☐", row["occurred_at"][:19].replace("T", " "),
                                      row["camera_name"], row["workspace"], missing, row["track_id"]))
                if selected and selected[0] in self._rows:
                    self.table.selection_set(selected[0])
                self._signature = signature
            self.update_checks()
            self.count.configure(text=f"Событий: {total} · Страница {self._page+1}" if total else "Событий пока нет")
            self.previous.configure(state="normal" if self._page else "disabled")
            self.next.configure(state="normal" if (self._page+1)*self.PAGE_SIZE < total else "disabled")
        except (OSError, sqlite3.Error) as exc:
            self.count.configure(text=f"Ошибка журнала: {exc}")

    def update_checks(self):
        for event_id in self._rows:
            self.table.set(event_id, "check", "☑" if event_id in self._checked else "☐")
        self.table.heading("check", text="☑" if self._rows and self._checked == set(self._rows) else "☐")
        self.delete_button.configure(text=f"Удалить выбранные ({len(self._checked)})",
                                     state="normal" if self._checked else "disabled")

    def toggle_all(self):
        self._checked = set() if self._checked == set(self._rows) else set(self._rows)
        self.update_checks()

    def toggle_row(self, event_id):
        if event_id in self._rows:
            self._checked.symmetric_difference_update({event_id})
            self.update_checks()

    def toggle_click(self, event):
        if self.table.identify_region(event.x, event.y) == "cell" and self.table.identify_column(event.x) == "#1":
            event_id = self.table.identify_row(event.y)
            self.table.focus(event_id)
            self.table.selection_set(event_id)
            self.toggle_row(event_id)
            return "break"

    def toggle_focused(self, _event):
        self.toggle_row(self.table.focus())
        return "break"

    def double_click(self, event):
        if self.table.identify_column(event.x) != "#1":
            self.preview()
        return "break"

    def delete_selected(self):
        event_ids = tuple(self._checked)
        if not event_ids:
            return
        if not messagebox.askyesno("Удалить события",
                                  f"Удалить выбранные события ({len(event_ids)}) и их фотографии?\n"
                                  "Это действие нельзя отменить.", parent=self, default="no"):
            return
        try:
            self.events.delete(event_ids)
        except (OSError, sqlite3.Error, ValueError) as exc:
            messagebox.showerror("Удаление событий", f"Не все события удалены: {exc}", parent=self)
        finally:
            for window in self._previews:
                if window.winfo_exists() and window.event_id in event_ids:
                    window.destroy()
            self._signature = None
            self.refresh()

    def _schedule(self):
        self._after_id = self.after(1500, self._poll)

    def _poll(self):
        if self.winfo_viewable():
            self.refresh()
        self._schedule()

    def preview(self):
        selected = self.table.selection()
        if not selected or selected[0] not in self._rows:
            return
        row = self._rows[selected[0]]
        try:
            with Image.open(self.events.snapshot_path(row)) as source:
                image = source.convert("RGB")
            image.thumbnail((1000, 600))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Снимок события", f"Фото недоступно: {exc}", parent=self)
            return
        window = ctk.CTkToplevel(self)
        window.event_id = row["id"]
        window.title(f"Событие — {row['camera_name']}")
        window.geometry(f"{max(620, image.width+32)}x{image.height+135}")
        window.transient(self.winfo_toplevel())
        photo = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
        window.photo = photo
        ctk.CTkLabel(window, text="", image=photo).pack(padx=12, pady=8)
        missing = ", ".join(RULE_LABELS[key] for key in json.loads(row["missing"]))
        details = (f"{row['occurred_at']} · {row['workspace']} / {row['camera_name']} · Трек {row['track_id']}\n"
                   f"Не обнаружено: {missing}\nСтатус: требует проверки оператором")
        ctk.CTkLabel(window, text=details, justify="left", anchor="w", font=font(12), wraplength=max(580, image.width)).pack(padx=12, fill="x")
        self._previews = [item for item in self._previews if item.winfo_exists()]
        self._previews.append(window)

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="Экспорт журнала", defaultextension=".csv",
                                           initialfile="events.csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                self.events.export_csv(Path(path), *self.filters())
                messagebox.showinfo("Журнал событий", "События по выбранному фильтру экспортированы.", parent=self)
            except (OSError, sqlite3.Error) as exc:
                messagebox.showerror("Экспорт", str(exc), parent=self)

    def close(self):
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        for window in self._previews:
            if window.winfo_exists():
                window.destroy()
