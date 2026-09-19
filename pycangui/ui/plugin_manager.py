# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Getting a plugin, and getting rid of one.

Installing is the only thing pycangui does that runs somebody else's code on
purpose, so it is the only place with a question in front of it that cannot be
switched off. Not a scare -- a plugin is the point, and most people installing
one wrote it -- but the fact, said once, at the moment it is true: this is
Python, it runs as part of the tool, and it can reach everything the tool can.

The dialog behind *Manage plugins...* is deliberately two lists. "What have I
got" and "what could I have" are different questions and people arrive with one
of them; a single list holding both, some of it real and some of it an offer,
answers neither well.

Nothing here reloads anything. Every operation says what it changed and the
window reloads the plugins, because a reload is what *Reload plugins* already
does and having two ways to do it is how they come to differ.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import plugin_package
from pycangui.core.context import Context
from pycangui.core.plugins import NO_VERSION, Plugins, supplied
from pycangui.ui import folders, messages

#: Said before every install, and not remembered. Each one is a different
#: file from a different person, so "you agreed last time" is not an answer.
WHAT_A_PLUGIN_IS = (
    "A plugin is Python that runs as part of pycangui, with everything pycangui "
    "can reach: the bus, your files and the network. Install ones you would be "
    "willing to run yourself."
)

REPLACING = (
    "This replaces {label} {old}, which is installed in this workspace now.\n"
    "Anything you have edited into it is lost."
)
REPLACING_EDITED = (
    "This replaces {label} {old}, which is installed in this workspace now.\n"
    "That copy has been changed since it was installed, so it is moved aside "
    "and kept rather than lost."
)
GOING_BACK = "The one you are installing ({new}) is older than the one installed ({old})."

#: Said at the same time, because "what will this do to my window" is the other
#: half of the question and the answer is not obvious: whatever it adds is a
#: pane like any other, and stays until it is taken away.
WHAT_IT_STAYS = (
    "Whatever it adds -- a pane, menu entries, a button -- belongs to this "
    "workspace and stays until you remove it or switch it off."
)

#: Where the fingerprints of installed plugins live: the workspace, not
#: the plugin folders, so exporting one carries no bookkeeping with it.
AS_INSTALLED = "plugins.as_installed"

UPDATE_TIP = "This one is the same version as the one pycangui ships."
UPDATE_TO = (
    "Install version {version}, the one this pycangui ships, over the one\n"
    "in this workspace. Anything edited into the copy here is lost."
)
#: Said once a session, when a plugin in this workspace is behind the one
#: pycangui now ships. A workspace copy does not change when pycangui is
#: updated -- that is the point of it being a copy -- so nothing would
#: otherwise say that the drive pane gained anything.
BEHIND = (
    "Plugin {label} {installed} is installed here; this pycangui ships "
    "{supplied}. Tools > Plugins to update it."
)

UNINSTALL = (
    "Remove {label} from this workspace?\n\n"
    "Its folder is deleted, and anything you have edited into it goes with it. "
    "Export it first if you want to keep a copy.\n\n"
    "Its panes and menu entries go as soon as it is removed."
)

INACTIVE_TIP = (
    "Switched off plugins are not loaded at all: no pane, no menu entries, and\n"
    "none of their code runs. The folder stays, so whatever you edited into it\n"
    "is still there when you switch it back on."
)


def _version(text: str) -> str:
    return "" if text == NO_VERSION else text


class PluginActions(QObject):
    """Install, uninstall, export and switch off. One place, two callers.

    The menu and the dialog both want all of this, and an operation written
    twice is an operation that asks a different question depending on where it
    was reached from.
    """

    #: Something changed on disk or in the settings: reload. Carries the
    #: plugin to bring forward afterwards, or "" for none -- somebody who has
    #: just installed one should be shown it rather than told where to look.
    changed = Signal(str)

    def __init__(self, window: QWidget, ctx: Context, plugins: Plugins) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.plugins = plugins

    @property
    def folder(self) -> Path:
        return self.plugins.folder or (self.ctx.workspace_dir / "plugins")

    # --- getting one -----------------------------------------------------------------
    def install_file(self, parent: QWidget | None = None) -> None:
        """Install from a package somebody sent."""
        path = folders.open_file(
            parent or self.window,
            self.ctx,
            folders.PLUGIN,
            "Install plugin",
            plugin_package.FILTER,
            self.ctx.user_dir,
        )
        if path:
            self._install(
                parent,
                lambda: plugin_package.inspect(path),
                lambda replace: plugin_package.install(path, self.folder, replace),
            )

    def install_supplied(self, name: str, parent: QWidget | None = None) -> None:
        """Install one of the ones pycangui ships.

        Packed and unpacked rather than copied, so that the supplied plugins go
        in through exactly the code a downloaded one does.
        """
        entry = next((s for s in supplied() if s.name == name), None)
        if entry is None:
            return
        self._install(
            parent,
            lambda: plugin_package.inspect_folder(entry.folder),
            lambda replace: plugin_package.install_folder(entry.folder, self.folder, replace),
        )

    def _install(self, parent, look, doit) -> None:
        """Ask, then install, then say what happened.

        Looked at before it is installed so that the question can name what is
        actually in the package rather than what its filename suggests -- and
        so that a file which is not a plugin at all is refused before anybody
        is asked to agree to anything.
        """
        try:
            package = look()
        except plugin_package.PackageError as why:
            self._refuse(parent, str(why))
            return

        old = plugin_package.installed(self.folder, package.name)
        edited = old is not None and self._edited(package.name)
        question = [f"Install {package.label}{self._label_version(package.info.version)}?", ""]
        if old is not None:
            shape = REPLACING_EDITED if edited else REPLACING
            question.append(
                shape.format(label=old.title or package.name, old=_version(old.version) or "?")
            )
            if _older(package.info.version, old.version):
                question.append(GOING_BACK.format(new=package.info.version, old=old.version))
            question.append("")
        question.append(WHAT_A_PLUGIN_IS)
        question.append("")
        question.append(WHAT_IT_STAYS)

        answer = messages.warning(
            parent or self.window,
            "Install plugin",
            "\n".join(question),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        # Moved aside rather than deleted, the same as a hook file's .bak:
        # what is being replaced is somebody's own work, and an install that
        # threw it away would be the one operation here with no way back.
        kept = None
        if edited:
            try:
                kept = plugin_package.keep_a_copy(self.folder / package.name)
            except OSError as why:
                self._refuse(parent, f"{package.name} could not be moved aside: {why}")
                return
        try:
            doit(True)
        except plugin_package.PackageError as why:
            self._refuse(parent, str(why))
            return
        except OSError as why:
            self._refuse(parent, f"{package.name} could not be unpacked: {why}")
            return
        self.set_active(package.name, True, quiet=True)
        self._note_as_installed(package.name)
        self.ctx.events.information(
            f"Installed plugin {package.label}{self._label_version(package.info.version)}"
        )
        if kept is not None:
            self.ctx.events.information(f"The copy you had was kept as {kept.name}")
        self.changed.emit(package.name)

    @staticmethod
    def _label_version(version: str) -> str:
        return f" {version}" if _version(version) else ""

    def _refuse(self, parent, why: str) -> None:
        self.ctx.events.warning(why)
        messages.warning(parent or self.window, "Install plugin", why)

    # --- and losing one ----------------------------------------------------------------
    def uninstall(self, name: str, parent: QWidget | None = None) -> None:
        record = self.plugins.loaded.get(name)
        label = record.label if record else name
        answer = messages.warning(
            parent or self.window,
            "Remove plugin",
            UNINSTALL.format(label=label),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            gone = plugin_package.uninstall(self.folder, name)
        except (OSError, plugin_package.PackageError) as why:
            self._refuse(parent, f"{label} could not be removed: {why}")
            return
        if gone:
            self.plugins.disabled.discard(name)
            self._save_disabled()
            self.ctx.events.information(f"Removed plugin {label}")
            self.changed.emit("")

    def export(self, name: str, parent: QWidget | None = None) -> None:
        """Write an installed plugin out as a package, ready to send."""
        record = self.plugins.loaded.get(name)
        version = _version(record.version) if record else ""
        suggested = f"{name}-{version}{plugin_package.SUFFIX}" if version else f"{name}.zip"
        path = folders.save_file(
            parent or self.window,
            self.ctx,
            folders.PLUGIN,
            "Export plugin",
            plugin_package.FILTER,
            self.ctx.user_dir,
            suggested,
        )
        if not path:
            return
        try:
            plugin_package.pack(self.folder / name, path)
        except (OSError, plugin_package.PackageError) as why:
            self._refuse(parent, f"{name} could not be packed: {why}")
            return
        self.ctx.events.information(f"Exported plugin {name} to {path}")

    # --- and switching one off -----------------------------------------------------------
    def set_active(self, name: str, on: bool, quiet: bool = False) -> None:
        """Switch a plugin on or off. Off means not loaded at all."""
        if on:
            self.plugins.disabled.discard(name)
        else:
            self.plugins.disabled.add(name)
        self._save_disabled()
        if not quiet:
            self.ctx.events.information(f"Plugin {name} switched {'on' if on else 'off'}")
            self.changed.emit(name if on else "")

    def _save_disabled(self) -> None:
        self.ctx.settings.set("plugins.disabled", sorted(self.plugins.disabled))

    # --- what is on offer ------------------------------------------------------------------
    def not_installed(self) -> list:
        """The supplied plugins this workspace has not got."""
        here = set(self.plugins.found())
        return [s for s in supplied() if s.name not in here]

    # --- a copy somebody has worked on ---------------------------------------------
    def _as_installed(self) -> dict:
        saved = self.ctx.settings.get(AS_INSTALLED, {})
        return dict(saved) if isinstance(saved, dict) else {}

    def _note_as_installed(self, name: str) -> None:
        """Remember what this plugin looked like when it went in.

        In the workspace's settings rather than in the plugin folder, so
        that exporting one does not carry pycangui's bookkeeping along to
        whoever it is sent to.
        """
        folder = self.folder / name
        if folder.is_dir():
            record = self._as_installed()
            record[name] = plugin_package.folder_fingerprint(folder)
            self.ctx.settings.set(AS_INSTALLED, record)

    def _edited(self, name: str) -> bool:
        """Whether the copy here differs from the one that was installed.

        Unknown counts as edited: a plugin installed before any of this
        existed has no record, and treating that as untouched would throw
        away exactly the copy this is here to keep.
        """
        folder = self.folder / name
        if not folder.is_dir():
            return False
        return self._as_installed().get(name) != plugin_package.folder_fingerprint(folder)

    def out_of_date(self) -> dict[str, tuple[str, str]]:
        """name -> (installed version, the version pycangui now ships).

        A plugin is installed into a workspace, which is a copy: pulling a
        newer pycangui leaves that copy exactly as it was, and nothing said
        so. Reported rather than acted on -- the copy may be one somebody
        has edited, and replacing it is what the install question is for.
        """
        out = {}
        for entry in supplied():
            record = self.plugins.loaded.get(entry.name)
            if record is None:
                continue
            if _older(record.version, entry.info.version):
                out[entry.name] = (record.version, entry.info.version)
        return out


def _older(new: str, old: str) -> bool:
    """Whether one version is behind another, for the two that look like numbers.

    Deliberately shallow. A plugin's version is whatever its author wrote, and
    guessing an order for two strings that are not numbers would produce a
    confident warning about nothing. Anything this cannot compare is simply
    not remarked on.
    """
    try:
        return _parts(new) < _parts(old)
    except ValueError:
        return False


def _parts(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


class ManagePlugins(QDialog):
    """What is installed, what could be, and the buttons for both."""

    def __init__(self, parent: QWidget, actions: PluginActions) -> None:
        super().__init__(parent)
        self.actions = actions
        self.setWindowTitle("Manage plugins")
        self.resize(560, 520)
        self._filling = False

        self.installed = QListWidget()
        self.installed.setSelectionMode(QAbstractItemView.SingleSelection)
        self.installed.setToolTip(INACTIVE_TIP)
        self.installed.itemChanged.connect(self._toggled)
        self.installed.currentItemChanged.connect(lambda *_a: self._enable_buttons())

        install_file = QPushButton("Install from file...")
        install_file.setToolTip("A plugin package: a zip with a plugin.py in it.")
        install_file.clicked.connect(self._install_file)
        self.export_button = QPushButton("Export...")
        self.export_button.setToolTip("Write this one out as a package, to send or to keep.")
        self.export_button.clicked.connect(self._export)
        self.remove_button = QPushButton("Remove...")
        self.remove_button.setToolTip("Delete it from this workspace, edits and all.")
        self.remove_button.clicked.connect(self._uninstall)
        self.update_button = QPushButton("Update...")
        self.update_button.setToolTip(UPDATE_TIP)
        self.update_button.clicked.connect(self._update)

        mine = QHBoxLayout()
        mine.addWidget(install_file)
        mine.addStretch()
        mine.addWidget(self.update_button)
        mine.addWidget(self.export_button)
        mine.addWidget(self.remove_button)

        self.offered = QListWidget()
        self.offered.setSelectionMode(QAbstractItemView.SingleSelection)
        self.offered.currentItemChanged.connect(lambda *_a: self._enable_buttons())
        self.offered.itemDoubleClicked.connect(lambda _i: self._install_supplied())
        self.add_button = QPushButton("Install")
        self.add_button.clicked.connect(self._install_supplied)

        theirs = QHBoxLayout()
        theirs.addStretch()
        theirs.addWidget(self.add_button)

        note = QLabel(WHAT_A_PLUGIN_IS)
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Installed in this workspace (tick to switch on)"))
        layout.addWidget(self.installed, 1)
        layout.addLayout(mine)
        layout.addSpacing(8)
        layout.addWidget(QLabel("Supplied with pycangui"))
        layout.addWidget(self.offered)
        layout.addLayout(theirs)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.refresh()

    # --- what it shows ------------------------------------------------------------------
    def refresh(self) -> None:
        self._filling = True
        self.installed.clear()
        behind = self.actions.out_of_date()
        for record in sorted(self.actions.plugins.loaded.values(), key=lambda r: r.label.lower()):
            text = record.label + PluginActions._label_version(record.version)
            if not record.ok:
                text += "  (failed to load -- why is in the Event Log)"
            if (newer := behind.get(record.name)) is not None:
                text += f"  --  {newer[1]} supplied with this pycangui"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, record.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if record.active else Qt.Unchecked)
            item.setToolTip(record.description or f"{record.name}/")
            self.installed.addItem(item)
        if self.installed.count() == 0:
            empty = QListWidgetItem("Nothing installed in this workspace yet.")
            empty.setFlags(Qt.NoItemFlags)
            self.installed.addItem(empty)

        self.offered.clear()
        for entry in self.actions.not_installed():
            text = entry.label + PluginActions._label_version(entry.info.version)
            if entry.info.description:
                text += f"  --  {entry.info.description}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry.name)
            self.offered.addItem(item)
        if self.offered.count() == 0:
            done = QListWidgetItem("All of them are installed here.")
            done.setFlags(Qt.NoItemFlags)
            self.offered.addItem(done)
        self._filling = False
        self._enable_buttons()

    def _chosen(self, listing: QListWidget) -> str:
        item = listing.currentItem()
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    def _enable_buttons(self) -> None:
        mine = self._chosen(self.installed)
        self.export_button.setEnabled(bool(mine))
        self.remove_button.setEnabled(bool(mine))
        # Only where there is something newer to install: an Update button
        # that is always live invites pressing it to find out.
        behind = self.actions.out_of_date()
        self.update_button.setEnabled(mine in behind)
        self.update_button.setToolTip(
            UPDATE_TO.format(version=behind[mine][1]) if mine in behind else UPDATE_TIP
        )
        self.add_button.setEnabled(bool(self._chosen(self.offered)))

    # --- and what it does -------------------------------------------------------------
    def _toggled(self, item: QListWidgetItem) -> None:
        if self._filling:
            return
        name = str(item.data(Qt.UserRole) or "")
        if name:
            self.actions.set_active(name, item.checkState() == Qt.Checked)
            self.refresh()

    def _install_file(self) -> None:
        self.actions.install_file(self)
        self.refresh()

    def _update(self) -> None:
        """Install the supplied copy over the one in this workspace.

        The same question as any other install, warning that edits are
        lost: this is a replacement, not a merge, and the copy here may be
        one somebody has changed on purpose.
        """
        if name := self._chosen(self.installed):
            self.actions.install_supplied(name, self)

    def _install_supplied(self) -> None:
        if name := self._chosen(self.offered):
            self.actions.install_supplied(name, self)
            self.refresh()

    def _export(self) -> None:
        if name := self._chosen(self.installed):
            self.actions.export(name, self)

    def _uninstall(self) -> None:
        if name := self._chosen(self.installed):
            self.actions.uninstall(name, self)
            self.refresh()
