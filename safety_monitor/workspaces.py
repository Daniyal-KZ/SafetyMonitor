"""Traditional workspace/camera tree and administrator workspace actions."""

from __future__ import annotations

from tkinter import ttk
from typing import Callable

import customtkinter as ctk

from .dialogs import WorkspaceDialog
from .models import Camera, Workspace, WorkspaceStore
from .theme import BACKGROUND, BLUE, BORDER, MUTED, SURFACE, TEXT, font, button


class WorkspaceManager(ctk.CTkFrame):
    def __init__(self, master, store: WorkspaceStore,
                 on_select: Callable[[Workspace, Camera | None], None],
                 on_delete: Callable[[], None]) -> None:
        super().__init__(master, fg_color=SURFACE, corner_radius=0,
                         border_color=BORDER, border_width=1, width=240)
        self.store = store
        self.on_select = on_select
        self.on_delete = on_delete
        self.selected_workspace_id: str | None = None
        self.selected_camera_id: str | None = None
        self.dialog: WorkspaceDialog | None = None
        self.selection_dialog: ctk.CTkToplevel | None = None
        self._refreshing = False
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(self, text="Рабочие зоны", font=font(14, "bold"), text_color=TEXT,
                     anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 6))
        actions = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        actions.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1)
        for column, (text, command) in enumerate((("Создать…", self.create_workspace),
                                                   ("Открыть…", self.open_workspace))):
            ctk.CTkButton(actions, text=text, command=command, height=28, width=95,
                          state='disabled' if column == 0 and not store.can_manage else 'normal',
                          corner_radius=3, fg_color=BACKGROUND, text_color=TEXT,
                          border_color=BORDER, border_width=1, hover_color=BORDER,
                          font=font(12)).grid(row=0, column=column, sticky="ew", padx=(0, 4) if column == 0 else (4, 0))
        self.rename_button = button(actions, text='Изменить…', command=self.rename_workspace,
                                    width=95, height=28, fg_color=BACKGROUND, state='disabled')
        self.rename_button.grid(row=1, column=0, sticky='ew', padx=(0, 4), pady=(6, 0))
        self.delete_button = button(actions, text='Удалить…', command=self.on_delete,
                                    width=95, height=28, fg_color=BACKGROUND, state='disabled')
        self.delete_button.grid(row=1, column=1, sticky='ew', padx=(4, 0), pady=(6, 0))
        self.tree = ttk.Treeview(self, show="tree", selectmode="browse", style="Navigation.Treeview")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=(1, 0), pady=(0, 1))
        self.tree.column("#0", width=210, minwidth=100, stretch=True)
        scrollbar = ctk.CTkScrollbar(self, command=self.tree.yview, width=12)
        scrollbar.grid(row=2, column=1, sticky="ns", pady=(0, 1))
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", self._on_select)
        self.empty_label = ctk.CTkLabel(self, text="Нет рабочих зон", text_color=MUTED, font=font(12))
        self.empty_label.grid(row=3, column=0, columnspan=2, sticky="ew", pady=6)
        self.refresh()

    def refresh(self, workspace_id: str | None = None, camera_id: str | None = None) -> None:
        self._refreshing = True
        try:
            self.tree.delete(*self.tree.get_children())
            for workspace in self.store.workspaces:
                self.tree.insert("", "end", iid=f"w:{workspace.id}", text=workspace.name, open=True)
                for camera in workspace.cameras:
                    self.tree.insert(f"w:{workspace.id}", "end", iid=f"c:{camera.id}", text=camera.name)
            self.empty_label.configure(text="Нет рабочих зон" if not self.store.workspaces
                                       else f"Зон: {len(self.store.workspaces)}")
            if workspace_id is not None:
                self.selected_workspace_id = workspace_id
                self.selected_camera_id = camera_id
            if not any(w.id == self.selected_workspace_id for w in self.store.workspaces):
                self.selected_workspace_id = self.selected_camera_id = None
            selection = f"c:{self.selected_camera_id}" if self.selected_camera_id else f"w:{self.selected_workspace_id}"
            if self.tree.exists(selection):
                self.tree.selection_set(selection)
                self.tree.see(selection)
        finally:
            self._refreshing = False
            self._update_actions()

    def _update_actions(self):
        state = 'normal' if self.store.can_manage and self.selected_workspace_id else 'disabled'
        self.rename_button.configure(state=state)
        self.delete_button.configure(state=state)

    def _on_select(self, _event=None) -> None:
        if self._refreshing:
            return
        selection = self.tree.selection()
        if not selection:
            return
        item = selection[0]
        if item.startswith("w:"):
            workspace = self.store.get_workspace(item[2:])
            camera = None
        else:
            workspace = self.store.get_workspace(self.tree.parent(item)[2:])
            camera = next(value for value in workspace.cameras if value.id == item[2:])
        camera_id = camera.id if camera else None
        if (workspace.id, camera_id) == (self.selected_workspace_id, self.selected_camera_id):
            return
        self.selected_workspace_id = workspace.id
        self.selected_camera_id = camera_id
        self._update_actions()
        self.on_select(workspace, camera)

    def select_workspace(self, workspace_id: str) -> None:
        self.refresh(workspace_id)
        workspace = self.store.get_workspace(workspace_id)
        self.on_select(workspace, None)

    def create_workspace(self) -> None:
        if not self.store.can_manage:
            return
        if self.dialog is not None and self.dialog.winfo_exists():
            self.dialog.focus_set()
            return
        self.dialog = WorkspaceDialog(self, self._create)

    def _create(self, name: str) -> tuple:
        return lambda store: store.add_workspace(name), lambda workspace: self.select_workspace(workspace.id)

    def rename_workspace(self) -> None:
        if not self.store.can_manage:
            return
        if self.selected_workspace_id is None:
            return
        workspace = self.store.get_workspace(self.selected_workspace_id)
        self.dialog = WorkspaceDialog(self, lambda name: self._rename(workspace.id, name),
                                       initial_name=workspace.name, title="Переименовать рабочую зону")

    def _rename(self, workspace_id: str, name: str) -> tuple:
        return (lambda store: store.rename_workspace(workspace_id, name),
                lambda _: self.select_workspace(workspace_id))

    def open_workspace(self) -> None:
        if not self.store.workspaces:
            self.create_workspace()
            return
        if self.selection_dialog is not None and self.selection_dialog.winfo_exists():
            self.selection_dialog.focus_set()
            return
        dialog = ctk.CTkToplevel(self)
        self.selection_dialog = dialog
        dialog.title("Открыть рабочую зону")
        dialog.geometry("440x380")
        dialog.minsize(360, 280)
        dialog.transient(self.winfo_toplevel())
        dialog.configure(fg_color=BACKGROUND)
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(dialog, text="Выберите рабочую зону", font=font(14, "bold"),
                     text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="ew", padx=16, pady=12)
        tree = ttk.Treeview(dialog, show="tree", selectmode="browse", style="Navigation.Treeview")
        tree.grid(row=1, column=0, sticky="nsew", padx=16)
        for workspace in self.store.workspaces:
            tree.insert("", "end", iid=workspace.id, text=workspace.name)
        tree.selection_set(self.selected_workspace_id or self.store.workspaces[0].id)

        def open_selected(_event=None) -> None:
            if tree.selection():
                self.select_workspace(tree.selection()[0])
                dialog.destroy()

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="e", padx=16, pady=12)
        ctk.CTkButton(buttons, text="Отмена", width=95, height=30, corner_radius=3,
                      fg_color=SURFACE, text_color=TEXT, border_color=BORDER, border_width=1,
                      command=dialog.destroy).grid(row=0, column=0, padx=(0, 8))
        ctk.CTkButton(buttons, text="Открыть", width=95, height=30, corner_radius=3,
                      fg_color=BLUE, command=open_selected).grid(row=0, column=1)
        tree.bind("<Double-1>", open_selected)
        dialog.bind("<Return>", open_selected)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def close(self) -> None:
        for dialog in (self.dialog, self.selection_dialog):
            if dialog is not None and dialog.winfo_exists():
                dialog.destroy()
