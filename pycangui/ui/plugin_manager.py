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
answers neither well. What could be had comes from the plugin lists online
(see core.plugin_index), read only when *Check for plugins* is pressed.

Nothing here reloads anything. Every operation says what it changed and the
window reloads the plugins, because a reload is what *Reload plugins* already
does and having two ways to do it is how they come to differ.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import installation, plugin_index, plugin_package
from pycangui.core.context import Context
from pycangui.core.plugin_index import Offer
from pycangui.core.plugins import API_VERSION, ENTRY, NO_VERSION, Plugins, describe
from pycangui.core.worker import Worker
from pycangui.ui import folders, icons, messages, pip_install

#: Said before every install, and not remembered. Each one is a different
#: file from a different person, so "you agreed last time" is not an answer.
WHAT_A_PLUGIN_IS = (
    "A plugin is Python that runs as part of pycangui, with everything pycangui "
    "can reach: the bus, your files and the network. Install ones you would be "
    "willing to run yourself."
)

REPLACING = (
    "This replaces {label} {old}, which is installed in this workspace now.\n"
    "That copy has no changes of yours in it, so it is overwritten and "
    "nothing is lost."
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

#: Plugins pycangui once supplied that are now part of it: the folder they were
#: installed as, what they called themselves, and where they went.
NOW_BUILT_IN = {
    "firmware": (
        "CANopen firmware (CiA 302-3)",
        "the Firmware tab of the CANopen pane, and its sequence to the firmware hooks "
        "in hooks/canopen.py",
    ),
}
#: Which of those an edited copy has been said of, so it is said once.
BUILT_IN_SAID = "plugins.built_in_said"
MOVED_REMOVED = (
    "{label} is now part of pycangui: {where}. The plugin copy in this workspace "
    "had not been changed, so it has been removed."
)
MOVED_KEPT = (
    "{label} is now part of pycangui: {where}. The plugin copy in this workspace has "
    "been changed, so it is left as it was and still loads; copy what you changed in "
    "it into those hooks, then remove the plugin. Said once."
)

#: The extra plugin lists somebody has added, beside pycangui's own: kept for
#: every workspace, as a browser keeps its bookmarks.
SITES = "plugins/sites"

UPDATE_TIP = "Nothing newer is known. Check for plugins to look."
UPDATE_TO = (
    "Install version {version} from its list over the one in this workspace.\n"
    "A copy you have changed is moved aside and kept; one you have not is overwritten."
)
CHECK_TIP = (
    "Read the plugin lists online: pycangui's, and any sites you have added.\n"
    "Nothing is installed until you choose one, and nothing about you is sent."
)
SITES_TIP = "Other plugin lists to read beside pycangui's: a URL or a file each."
NOT_CHECKED = "Press Check for plugins to see what there is. It reads the plugin lists online."
NONE_MORE = "Every plugin the lists offer is installed here."
NEEDS_NEWER = (
    "{label} {version} is written for plugin API {api}, and this pycangui has "
    "plugin API {have}. A newer pycangui will install it."
)
NOT_WHAT_IT_SAID = (
    "{url} holds plugin {found}, not {name} as its list says, so it was not installed."
)
SITES_TEXT = (
    "Plugin lists to read beside pycangui's own, one per line: an https URL,\n"
    "or a file on this computer. Each is JSON in the format the manual describes."
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


#: Where pip can install what a plugin needs.
OFFER_PIP = (
    "{label} needs {needs}, which is not installed here.\n\n"
    "Install it now with pip? It is downloaded from the Python Package Index, "
    "and the plugin is loaded again once it is there."
)
#: The Windows build, which has no pip.
NO_PIP = (
    "{label} needs {needs}, which this build of pycangui does not have and cannot "
    "install: it has no pip.\n\n"
    "A plugin can bring a pure-Python package with it, in a lib folder beside its "
    "plugin.py. Otherwise it needs pycangui installed with pip."
)


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

    #: A check of the lists, or a download, has finished: show what there is.
    settled = Signal()

    def __init__(self, window: QWidget, ctx: Context, plugins: Plugins) -> None:
        super().__init__(window)
        self.window = window
        self.ctx = ctx
        self.plugins = plugins
        #: What the lists offered at the last check; None before any check.
        self.offers: list[Offer] | None = None
        #: What could not be read at the last check.
        self.problems: list[str] = []
        self.busy = False
        self._worker: Worker | None = None

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.stop()

    def _work(self) -> Worker:
        if self._worker is None:
            self._worker = Worker(self)
        return self._worker

    @property
    def folder(self) -> Path:
        return self.plugins.folder or (self.ctx.workspace_dir / "plugins")

    def retire_built_in(self) -> list[str]:
        """Plugins that are now part of pycangui: what to say about each.

        Asked before the plugins load. A copy nobody changed is pycangui's
        own, and goes: two of the same screen helps nobody. A changed one is
        somebody's work, possibly the only copy of it, so it stays and goes on
        loading, and is said once.
        """
        said = []
        told = list(self.ctx.settings.get(BUILT_IN_SAID, []) or [])
        for name, (label, where) in NOW_BUILT_IN.items():
            folder = self.folder / name
            entry = folder / ENTRY
            if not entry.is_file():
                continue
            if describe(entry).title != label:
                continue  # somebody's own plugin of the same folder name
            if not self._edited(name):
                plugin_package.uninstall(self.folder, name)
                record = self._as_installed()
                record.pop(name, None)
                self.ctx.settings.set(AS_INSTALLED, record)
                self.plugins.disabled.discard(name)
                self._save_disabled()
                said.append(MOVED_REMOVED.format(label=label, where=where))
            elif name not in told:
                told.append(name)
                self.ctx.settings.set(BUILT_IN_SAID, told)
                said.append(MOVED_KEPT.format(label=label, where=where))
        return said

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

    # --- from the lists online ---------------------------------------------------------
    def sites(self) -> list[str]:
        """The plugin lists somebody has added, beside pycangui's own."""
        saved = QSettings().value(SITES, [])
        if isinstance(saved, str):
            saved = [saved]
        return [str(site).strip() for site in saved or [] if str(site).strip()]

    def set_sites(self, sites: list[str]) -> None:
        QSettings().setValue(SITES, [site.strip() for site in sites if site.strip()])

    def lists(self) -> list[str]:
        return [plugin_index.INDEX, *self.sites()]

    def check(self) -> bool:
        """Read the lists, off the GUI thread; ``settled`` says when. False if busy."""
        if self.busy:
            return False
        self.busy = True
        lists = self.lists()
        self._work().submit(lambda: plugin_index.read(lists), self._checked)
        return True

    def _checked(self, result, error: str | None) -> None:
        self.busy = False
        offers, problems = result if error is None else ([], [error])
        self.offers, self.problems = offers, problems
        for problem in problems:
            self.ctx.events.warning(f"Plugin list: {problem}")
        self.ctx.events.information(f"The plugin lists offer {len(offers)} plugins")
        self.settled.emit()

    def offer(self, name: str) -> Offer | None:
        return next((o for o in self.offers or [] if o.name == name), None)

    def available(self) -> list[Offer]:
        """What the lists offer that this workspace has not got."""
        here = set(self.plugins.found())
        return [o for o in self.offers or [] if o.name not in here]

    def newer(self, name: str) -> Offer | None:
        """The offer of a newer version of an installed plugin, if the lists have one."""
        record = self.plugins.loaded.get(name)
        offer = self.offer(name)
        if record is None or offer is None:
            return None
        return offer if _older(record.version, offer.version) else None

    def install_offer(self, name: str, parent: QWidget | None = None) -> bool:
        """Download a plugin a list offers, then install it as any other.

        The download is checked against the list's sha256 before anything is
        asked; the install question, and keeping an edited copy aside, are the
        ones a zip from a file gets. False if nothing was started.
        """
        offer = self.offer(name)
        if offer is None or self.busy:
            return False
        if offer.api_version > API_VERSION:
            self._refuse(
                parent,
                NEEDS_NEWER.format(
                    label=offer.label,
                    version=offer.version,
                    api=offer.api_version,
                    have=API_VERSION,
                ),
            )
            return False
        self.busy = True
        folder = Path(tempfile.mkdtemp(prefix="pycangui-plugin-"))

        def job():
            try:
                return plugin_index.download(offer, folder), ""
            except plugin_index.ListError as why:
                return None, str(why)

        self._work().submit(
            job, lambda result, error: self._downloaded(offer, folder, parent, result, error)
        )
        return True

    def _downloaded(self, offer: Offer, folder: Path, parent, result, error: str | None) -> None:
        self.busy = False
        try:
            path, problem = result if error is None else (None, error)
            if path is None:
                self._refuse(parent, problem)
                return

            def look():
                package = plugin_package.inspect(path)
                if package.name != offer.name:
                    raise plugin_package.PackageError(
                        NOT_WHAT_IT_SAID.format(url=offer.url, found=package.name, name=offer.name)
                    )
                return package

            self._install(
                parent, look, lambda replace: plugin_package.install(path, self.folder, replace)
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            self.settled.emit()

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
        self.plugins.unload(package.name)  # what it runs would hold its folder open
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
        self.offer_packages(package.name, parent)

    # --- the packages it needs ------------------------------------------------------------
    def offer_packages(self, name: str, parent: QWidget | None = None, command=None) -> bool:
        """Offer to install with pip what a plugin's REQUIRES names and this lacks.

        Asked, not done: a download is not something to start unasked. The
        Windows build has no pip, so there it says what is missing and how a
        plugin can bring a pure-Python package itself. True if anything was
        installed and the plugins were loaded again. ``command`` replaces pip,
        for tests.
        """
        record = self.plugins.loaded.get(name)
        if record is None or not record.missing:
            return False
        needs = ", ".join(record.missing)
        where = parent or self.window
        if installation.how_installed() == installation.FROZEN:
            messages.information(
                where, "Packages a plugin needs", NO_PIP.format(label=record.label, needs=needs)
            )
            return False
        answer = messages.question(
            where,
            "Packages a plugin needs",
            OFFER_PIP.format(label=record.label, needs=needs),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return False
        if not pip_install.install(where, self.ctx, list(record.missing), command):
            return False
        self.ctx.events.information(f"Installed {needs} for plugin {record.label}")
        self.changed.emit(name)
        return True

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
        self.plugins.unload(name)  # what it runs would hold its folder open
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

        install_file = icons.beside(QPushButton("Install from file..."), icons.DOWNLOAD)
        install_file.setToolTip("A plugin package: a zip with a plugin.py in it.")
        install_file.clicked.connect(self._install_file)
        self.export_button = icons.beside(QPushButton("Export..."), icons.EXPORT)
        self.export_button.setToolTip("Write this one out as a package, to send or to keep.")
        self.export_button.clicked.connect(self._export)
        self.remove_button = icons.beside(QPushButton("Remove..."), icons.REMOVE)
        self.remove_button.setToolTip("Delete it from this workspace, edits and all.")
        self.remove_button.clicked.connect(self._uninstall)
        self.packages_button = icons.beside(QPushButton("Install packages..."), icons.DOWNLOAD)
        self.packages_button.setToolTip("Install with pip what this plugin needs and is not here.")
        self.packages_button.clicked.connect(self._packages)
        self.update_button = icons.beside(QPushButton("Update..."), icons.RELOAD)
        self.update_button.setToolTip(UPDATE_TIP)
        self.update_button.clicked.connect(self._update)

        mine = QHBoxLayout()
        mine.addWidget(install_file)
        mine.addStretch()
        mine.addWidget(self.packages_button)
        mine.addWidget(self.update_button)
        mine.addWidget(self.export_button)
        mine.addWidget(self.remove_button)

        self.offered = QListWidget()
        self.offered.setSelectionMode(QAbstractItemView.SingleSelection)
        self.offered.currentItemChanged.connect(lambda *_a: self._enable_buttons())
        self.offered.itemDoubleClicked.connect(lambda _i: self._install_offered())
        self.check_button = icons.beside(QPushButton("Check for plugins"), icons.UPDATES)
        self.check_button.setToolTip(CHECK_TIP)
        self.check_button.clicked.connect(self._check)
        sites = icons.beside(QPushButton("Sites..."), icons.MANAGE)
        sites.setToolTip(SITES_TIP)
        sites.clicked.connect(self._sites)
        self.add_button = icons.beside(QPushButton("Install"), icons.DOWNLOAD)
        self.add_button.clicked.connect(self._install_offered)

        theirs = QHBoxLayout()
        theirs.addWidget(self.check_button)
        theirs.addWidget(sites)
        theirs.addStretch()
        theirs.addWidget(self.add_button)
        actions.settled.connect(self.refresh)

        note = QLabel(WHAT_A_PLUGIN_IS)
        note.setWordWrap(True)
        note.setEnabled(False)

        buttons = icons.on_buttons(QDialogButtonBox(QDialogButtonBox.Close))
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Installed in this workspace (tick to switch on)"))
        layout.addWidget(self.installed, 1)
        layout.addLayout(mine)
        layout.addSpacing(8)
        layout.addWidget(QLabel("Available from the plugin lists"))
        layout.addWidget(self.offered)
        layout.addLayout(theirs)
        layout.addWidget(note)
        layout.addWidget(buttons)

        self.refresh()

    # --- what it shows ------------------------------------------------------------------
    def refresh(self) -> None:
        self._filling = True
        self.installed.clear()
        for record in sorted(self.actions.plugins.loaded.values(), key=lambda r: r.label.lower()):
            text = record.label + PluginActions._label_version(record.version)
            if not record.ok:
                text += "  (failed to load -- why is in the Event Log)"
            if (newer := self.actions.newer(record.name)) is not None:
                text += f"  --  {newer.version} available"
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
        for offer in self.actions.available():
            text = offer.label + PluginActions._label_version(offer.version)
            if offer.description:
                text += f"  --  {offer.description}"
            if offer.api_version > API_VERSION:
                text += "  (needs a newer pycangui)"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, offer.name)
            item.setToolTip(f"From {offer.source}" + (f"\n{offer.page}" if offer.page else ""))
            self.offered.addItem(item)
        if self.offered.count() == 0:
            done = QListWidgetItem(NOT_CHECKED if self.actions.offers is None else NONE_MORE)
            done.setFlags(Qt.NoItemFlags)
            self.offered.addItem(done)
        busy = self.actions.busy
        self.check_button.setEnabled(not busy)
        self.check_button.setText("Checking..." if busy else "Check for plugins")
        self._filling = False
        self._enable_buttons()

    def _chosen(self, listing: QListWidget) -> str:
        item = listing.currentItem()
        return str(item.data(Qt.UserRole) or "") if item is not None else ""

    def _enable_buttons(self) -> None:
        mine = self._chosen(self.installed)
        self.export_button.setEnabled(bool(mine))
        self.remove_button.setEnabled(bool(mine))
        record = self.actions.plugins.loaded.get(mine) if mine else None
        self.packages_button.setEnabled(bool(record and record.missing))
        # Only where there is something newer to install: an Update button
        # that is always live invites pressing it to find out.
        newer = self.actions.newer(mine) if mine else None
        self.update_button.setEnabled(newer is not None and not self.actions.busy)
        self.update_button.setToolTip(
            UPDATE_TO.format(version=newer.version) if newer is not None else UPDATE_TIP
        )
        self.add_button.setEnabled(bool(self._chosen(self.offered)) and not self.actions.busy)

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

    def _packages(self) -> None:
        if name := self._chosen(self.installed):
            self.actions.offer_packages(name, self)
            self.refresh()

    def _update(self) -> None:
        """Install the newer version a list offers over the one in this workspace.

        The same question as any other install: this is a replacement, not a
        merge, and a copy somebody has changed is moved aside and kept.
        """
        if name := self._chosen(self.installed):
            self.actions.install_offer(name, self)
            self.refresh()

    def _install_offered(self) -> None:
        if name := self._chosen(self.offered):
            self.actions.install_offer(name, self)
            self.refresh()

    def _check(self) -> None:
        self.actions.check()
        self.refresh()

    def _sites(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self, "Plugin lists", SITES_TEXT, "\n".join(self.actions.sites())
        )
        if ok:
            self.actions.set_sites(text.splitlines())

    def _export(self) -> None:
        if name := self._chosen(self.installed):
            self.actions.export(name, self)

    def _uninstall(self) -> None:
        if name := self._chosen(self.installed):
            self.actions.uninstall(name, self)
            self.refresh()
