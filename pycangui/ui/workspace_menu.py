# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""File > Workspace: save as, switch to, manage.

The whole surface, and deliberately three items.  There is no Save and no
unsaved-changes marker, because a workspace saves continuously -- which is
what settings.json has always done, and pycangui has never asked anybody to
save anything.  Adding a Save here would invent a question the tool does not
have an answer to.

Somebody with one product should be able to work for a year without opening
this menu.  That is why the workspace is called ``default``, why the title bar
says nothing while it is, and why *Switch to* shows one entry with a tick
against it rather than an empty list inviting a decision.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from pycangui.core import workspaces
from pycangui.core.context import Context
from pycangui.ui import messages

SAVE_AS_TIP = (
    "Keep everything as it is now -- the panes, the channels, the databases,\n"
    "the hooks and the EDS files -- under a new name, and carry on in that.\n"
    "The one you were in is left exactly as you left it."
)
SWITCH_TIP = (
    "Open another workspace.  Everything reloads, because a workspace holds\n"
    "which channels at what bitrate, and the adapters can only be in one of\n"
    "those states at a time."
)
MANAGE_TIP = "Rename or delete a workspace."

SWITCH_WHILE_CONNECTED = (
    "{name} is connected.\n\n"
    "Opening another workspace closes every channel and reopens whatever that "
    "workspace has, which means dropping off the bus you are on now.  Anything "
    "being transmitted cyclically stops.\n\n"
    "Open {target} anyway?"
)


class WorkspaceMenu(QObject):
    """The File submenu, and the small dialog behind Manage."""

    #: The workspace to open.  Acted on by the window, which cannot do it to
    #: itself: switching is a full reload, so the window that asks is the one
    #: that goes away.
    switch_requested = Signal(str)

    def __init__(self, window: QMainWindow, ctx: Context) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.menu = QMenu("Workspace")
        self.menu.setToolTipsVisible(True)
        self._build()

    def _build(self) -> None:
        """Rebuilt each time it opens, so a workspace made elsewhere is in it."""
        self.menu.clear()
        save_as = self.menu.addAction("Save as...", self._save_as)
        save_as.setToolTip(SAVE_AS_TIP)

        switch = self.menu.addMenu("Switch to")
        switch.setToolTipsVisible(True)
        switch.setToolTip(SWITCH_TIP)
        for name in workspaces.names():
            action = switch.addAction(name, lambda n=name: self._switch(n))
            action.setCheckable(True)
            action.setChecked(name == self.ctx.workspace)
            if name == self.ctx.workspace:
                action.setToolTip("The one you are in.")

        manage = self.menu.addAction("Manage...", self._manage)
        manage.setToolTip(MANAGE_TIP)

    def install(self, file_menu: QMenu) -> None:
        file_menu.addMenu(self.menu)
        self.menu.aboutToShow.connect(self._build)

    # --- the three things it does -----------------------------------------------------
    def _save_as(self) -> None:
        name, chose = QInputDialog.getText(
            self.window,
            "Save workspace as",
            "A name for what is on screen now:",
            text=self._suggestion(),
        )
        if not chose:
            return
        if (reason := workspaces.why_not(name)) != "":
            messages.warning(self.window, "That name will not do", reason)
            return
        # Forked from the one in use, because "save as" means keep this and
        # call it something else.  An empty new workspace would throw away the
        # arrangement somebody was looking at when they asked.
        workspaces.create(name, copy_from=self.ctx.workspace)
        self.switch_requested.emit(workspaces.clean(name))

    def new_empty(self) -> None:
        """A workspace with nothing in it, which is what a reset amounts to.

        Not on this menu, because *New* and *Save as* side by side is a
        question asked of everybody who only ever wanted to fork the one they
        are in.  It is reached from Tools > Reset > Reset everything, where
        somebody is already asking for a clean slate.
        """
        name, chose = QInputDialog.getText(
            self.window,
            "New workspace",
            "A name for the new, empty workspace:",
            text=self._suggestion(),
        )
        if not chose:
            return
        if (reason := workspaces.why_not(name)) != "":
            messages.warning(self.window, "That name will not do", reason)
            return
        workspaces.create(name)  # not forked: starting with nothing is the point
        self.switch_requested.emit(workspaces.clean(name))

    def _suggestion(self) -> str:
        """A name that is free, so the dialog opens on something acceptable."""
        base = "workspace"
        if workspaces.why_not(base) == "":
            return base
        number = 2
        while workspaces.why_not(f"{base} {number}") != "":
            number += 1
        return f"{base} {number}"

    def _switch(self, name: str) -> None:
        if name != self.ctx.workspace:
            self.switch_requested.emit(name)

    def _manage(self) -> None:
        dialog = ManageWorkspaces(self.window, self.ctx.workspace)
        dialog.exec()
        self._build()


class ManageWorkspaces(QDialog):
    """Rename and delete.  Switching is in the menu, where it is one click."""

    def __init__(self, parent: QMainWindow, current: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage workspaces")
        self.current = current
        self.resize(380, 300)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())

        self.rename = QPushButton("Rename...")
        self.rename.clicked.connect(self._rename)
        self.delete = QPushButton("Delete...")
        self.delete.clicked.connect(self._delete)

        buttons = QHBoxLayout()
        buttons.addWidget(self.rename)
        buttons.addWidget(self.delete)
        buttons.addStretch()

        closer = QDialogButtonBox(QDialogButtonBox.Close)
        closer.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "The workspace in use cannot be renamed away from under the "
                "window or deleted; switch to another one first."
            )
        )
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        layout.addWidget(closer)
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for name in workspaces.names():
            label = f"{name}  (in use)" if name == self.current else name
            self.list.addItem(label)
            self.list.item(self.list.count() - 1).setData(Qt.UserRole, name)
        self.list.setCurrentRow(0)
        self._update_buttons()

    def selected(self) -> str:
        item = self.list.currentItem()
        return "" if item is None else item.data(Qt.UserRole)

    def _update_buttons(self) -> None:
        """Say what cannot be done by not offering it, and why on hover."""
        name = self.selected()
        default = name == workspaces.DEFAULT
        in_use = name == self.current
        self.rename.setEnabled(bool(name) and not default and not in_use)
        self.delete.setEnabled(bool(name) and not default and not in_use)
        why = ""
        if default:
            why = f"{workspaces.DEFAULT} is the one that is always there."
        elif in_use:
            why = "This is the workspace in use.  Switch to another one first."
        self.rename.setToolTip(why)
        self.delete.setToolTip(why)

    def _rename(self) -> None:
        old = self.selected()
        new, chose = QInputDialog.getText(self, "Rename workspace", "New name:", text=old)
        if not chose:
            return
        try:
            workspaces.rename(old, new)
        except ValueError as exc:
            messages.warning(self, "That name will not do", str(exc))
            return
        self._fill()

    def _delete(self) -> None:
        name = self.selected()
        answer = messages.warning(
            self,
            "Delete this workspace?",
            f"{name} and everything in it -- its settings, its hooks and its "
            f"EDS files -- will be removed from disk.\n\nThis cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            workspaces.delete(name)
        except ValueError as exc:
            messages.warning(self, "Cannot delete that one", str(exc))
            return
        self._fill()
