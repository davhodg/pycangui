# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""File > Workspace: save as, switch to, manage, and export and import.

The first three are the whole of working with workspaces, and deliberately
so.  There is no Save and no unsaved-changes marker, because a workspace saves
continuously -- which is what settings.json has always done, and pycangui has
never asked anybody to save anything.  Adding a Save here would invent a
question the tool does not have an answer to.

Export and import sit below them, apart, because they are about moving a
workspace between computers rather than working in one.  Somebody with one
product on one machine can ignore both.

Somebody with one product should be able to work for a year without opening
this menu.  That is why the workspace is called ``default``, why the title bar
says nothing while it is, and why *Switch to* shows one entry with a tick
against it rather than an empty list inviting a decision.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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
    QWidget,
)

from pycangui.core import workspace_files, workspace_package, workspaces
from pycangui.core.context import Context
from pycangui.core.plugin_package import PackageError
from pycangui.core.settings import Settings
from pycangui.ui import folders, messages

SAVE_AS_TIP = (
    "Keep everything as it is now -- the panes, the channels, the databases,\n"
    "the hooks and the EDS files -- under a new name, and carry on in that.\n"
    "The one you were in is left exactly as you left it."
)
SWITCH_TIP = (
    "Open another workspace. Everything reloads, because a workspace holds\n"
    "which channels at what bitrate, and the adapters can only be in one of\n"
    "those states at a time."
)
MANAGE_TIP = "Rename, delete or export a workspace."
EXPORT_TIP = (
    "Write the workspace you are in out as one zip file, to share\n"
    "or to keep. Backups and what belongs to this computer are left out."
)
IMPORT_TIP = (
    "Make a new workspace from a zip file somebody exported, once you have seen\n"
    "what it will write. Nothing you already have is changed."
)

SWITCH_WHILE_CONNECTED = (
    "{name} is connected.\n\n"
    "Opening another workspace closes every channel and reopens whatever that "
    "workspace has, which means dropping off the bus you are on now. Anything "
    "being transmitted cyclically stops.\n\n"
    "Open {target} anyway?"
)

IMPORT_QUESTION = "A new workspace called {name} will be made from {file}, with {count} in it:"
#: Said when there is Python in it, because opening the workspace runs it: the
#: same fact the plugin installer states, at the moment it becomes true.
IMPORT_CODE = (
    "Some of it is Python -- hooks, simulated nodes or plugins -- which runs as "
    "part of pycangui once the workspace is open. Import workspaces you would be "
    "willing to run yourself."
)
IMPORT_CHANGES_NOTHING = "Nothing you already have is changed."
#: Past this many the list stops being read and starts hiding the question.
LISTED = 20


def _count(number: int) -> str:
    return f"{number} file" if number == 1 else f"{number} files"


class WorkspaceMenu(QObject):
    """The File submenu, and the small dialog behind Manage."""

    #: The workspace to open.  Acted on by the window, which cannot do it to
    #: itself: switching is a full reload, so the window that asks is the one
    #: that goes away.
    switch_requested = Signal(str)
    #: Write down the arrangement on screen now.  The dock layout is otherwise
    #: saved only on the way out, and an export of the workspace in use should
    #: carry the panes as they are rather than as they were at the last start.
    arrangement_wanted = Signal()

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

        self.menu.addSeparator()
        export = self.menu.addAction("Export...", lambda: self.export())
        export.setToolTip(EXPORT_TIP)
        imported = self.menu.addAction("Import...", lambda: self.import_file())
        imported.setToolTip(IMPORT_TIP)

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
        return workspaces.next_free()

    def _switch(self, name: str) -> None:
        if name != self.ctx.workspace:
            self.switch_requested.emit(name)

    def _manage(self) -> None:
        dialog = ManageWorkspaces(self.window, self.ctx.workspace, export=self.export)
        dialog.exec()
        self._build()

    # --- moving one between computers ---------------------------------------------------
    def export(self, name: str = "", parent: QWidget | None = None) -> None:
        """Write a workspace out as one zip.  The one in use unless told otherwise."""
        owner = parent or self.window
        name = name or self.ctx.workspace
        if not self._files_travel(name, owner):
            return
        path = folders.save_file(
            owner,
            self.ctx,
            folders.WORKSPACE,
            "Export workspace",
            workspace_package.FILTER,
            self.ctx.user_dir,
            f"{name}{workspace_package.SUFFIX}",
        )
        if not path:
            return
        if name == self.ctx.workspace:
            self.arrangement_wanted.emit()
        try:
            workspace_package.pack(name, path)
        except (OSError, PackageError) as why:
            self._refuse(owner, "Export workspace", f"{name} could not be exported: {why}")
            return
        self.ctx.events.information(f"Exported workspace {name} to {path}")

    def _files_travel(self, name: str, owner: QWidget) -> bool:
        """Offer to copy in the files the workspace uses from outside it.  False is Cancel.

        People keep databases, A2L and EDS files where they keep them, and an
        export carries only the workspace folder -- so this is where somebody
        finds out which of theirs would not arrive, and chooses.  No exports
        with the links as they are, which on another computer lead nowhere.
        """
        folder = workspaces.dir_for(name)
        settings = (
            self.ctx.settings if name == self.ctx.workspace else Settings(folder / "settings.json")
        )
        workspace_files.tidy(settings, folder)
        away = workspace_files.outside(settings, folder)
        if not away:
            return True
        lines = [
            f"{name} uses {_count(len(away))} kept outside the workspace, which an export "
            "does not carry:",
            "",
            *(f"    {reference.label}: {path}" for reference, path in away[:LISTED]),
        ]
        if len(away) > LISTED:
            lines.append(f"    ...and {_count(len(away) - LISTED)} more")
        lines += [
            "",
            "Copy them into the workspace first?  No exports without them, and on "
            "another computer those links will not work.",
        ]
        answer = messages.question(
            owner,
            "Copy files into the workspace?",
            "\n".join(lines),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Yes:
            copied, missing = workspace_files.bring_in(settings, folder)
            if copied:
                self.ctx.events.information(
                    f"Copied into {name}: " + ", ".join(path.name for path in copied)
                )
            if missing:
                self.ctx.events.warning(
                    "Not there, so not copied, and still pointing where they did: "
                    + ", ".join(str(path) for path in missing)
                )
        return True

    def import_file(self) -> None:
        """Make a new workspace from a file somebody exported, and offer to open it.

        Not offered from the Manage dialog: opening the new workspace reloads
        the window, which is not something to do from under a dialog of its own.
        """
        path = folders.open_file(
            self.window,
            self.ctx,
            folders.WORKSPACE,
            "Import workspace",
            workspace_package.FILTER,
            self.ctx.user_dir,
        )
        if not path:
            return
        # Looked at before anything is asked, so that a file which is not a
        # workspace is refused before anybody is asked to agree to anything,
        # and so that the question can list what is really in it.
        try:
            package = workspace_package.inspect(path)
        except PackageError as why:
            self._refuse(self.window, "Import workspace", str(why))
            return
        name = self._import_name(package)
        if not name:
            return
        answer = messages.question(
            self.window,
            "Import workspace",
            self._what_import_writes(package, name),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            workspace_package.install(path, name)
        except (OSError, PackageError) as why:
            self._refuse(self.window, "Import workspace", f"{name} could not be imported: {why}")
            return
        self.ctx.events.information(f"Imported workspace {name} from {path}")
        self._build()
        answer = messages.question(
            self.window,
            "Open the imported workspace?",
            f"{name} is ready. Open it now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            # Through the same signal as Switch to, and so through the same
            # question when a bus is connected.
            self.switch_requested.emit(name)

    def _import_name(self, package: workspace_package.WorkspacePackage) -> str:
        """The file's own name if it is free, otherwise whatever somebody chooses.

        Asked rather than numbered silently: someone else's "drive" arriving as
        "drive 2" is somebody's guess about which one they will want to find,
        and they are right here to say.  The next free name is offered, so
        accepting it is one key.
        """
        name = package.name
        if (reason := workspaces.why_not(name)) == "":
            return name
        typed, chose = QInputDialog.getText(
            self.window,
            "Import workspace",
            f"{reason}\n\nA name for the imported workspace:",
            text=workspaces.next_free(name),
        )
        if not chose:
            return ""
        if (reason := workspaces.why_not(typed)) != "":
            messages.warning(self.window, "That name will not do", reason)
            return ""
        return workspaces.clean(typed)

    @staticmethod
    def _what_import_writes(package: workspace_package.WorkspacePackage, name: str) -> str:
        files = package.files
        lines = [
            IMPORT_QUESTION.format(
                name=name, file=Path(package.path).name, count=_count(len(files))
            ),
            "",
            *(f"    {entry}" for entry in files[:LISTED]),
        ]
        if len(files) > LISTED:
            lines.append(f"    ...and {_count(len(files) - LISTED)} more")
        lines += ["", f"They go in {workspaces.dir_for(name)}.", ""]
        if package.code:
            lines += [IMPORT_CODE, ""]
        lines.append(IMPORT_CHANGES_NOTHING)
        return "\n".join(lines)

    def _refuse(self, parent: QWidget, title: str, why: str) -> None:
        self.ctx.events.warning(why)
        messages.warning(parent, title, why)


class ManageWorkspaces(QDialog):
    """Rename, delete and export.  Switching is in the menu, where it is one click."""

    def __init__(
        self,
        parent: QMainWindow,
        current: str,
        export: Callable[[str, QWidget], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage workspaces")
        self.current = current
        self._export = export
        self.resize(380, 300)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(lambda _row: self._update_buttons())

        self.rename = QPushButton("Rename...")
        self.rename.clicked.connect(self._rename)
        self.delete = QPushButton("Delete...")
        self.delete.clicked.connect(self._delete)
        self.export = QPushButton("Export...")
        self.export.setToolTip("Write this one out as a zip file, to share or to keep.")
        self.export.clicked.connect(self._export_selected)

        buttons = QHBoxLayout()
        buttons.addWidget(self.rename)
        buttons.addWidget(self.delete)
        buttons.addStretch()
        buttons.addWidget(self.export)

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
        # Any of them, the one in use and default included: a copy leaves the
        # original exactly where it is.
        self.export.setEnabled(bool(name) and self._export is not None)
        why = ""
        if default:
            why = f"{workspaces.DEFAULT} is the one that is always there."
        elif in_use:
            why = "This is the workspace in use. Switch to another one first."
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

    def _export_selected(self) -> None:
        if (name := self.selected()) and self._export is not None:
            self._export(name, self)
