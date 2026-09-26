# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Choosing the seed and key DLL, and saying what is wrong with it early.

One dialog for both panes, because it is one file: an ECU ships one unlock
algorithm and UDS SecurityAccess and XCP unlocking are the same question
asked twice.

Most of this pane is about bitness, which is the thing that actually goes
wrong. These DLLs are generally built 32-bit, while pycangui runs 64-bit --
and no process can load a library of the other bitness, so the answer is to
run the DLL somewhere that can and pass the key back. That is what every
measurement tool does. Said here, on the pane where the DLL is chosen,
rather than discovered at the moment somebody is trying to unlock an ECU.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core import seedkey
from pycangui.core.context import Context

TITLE = "Seed and key DLL"

WHAT = (
    "The seed and key DLL, which is how an ECU's unlock algorithm ships. "
    "Used for UDS SecurityAccess and for XCP and CCP unlocking alike, and "
    "only when the matching hook returns None -- hooks/uds.py::security_key "
    "and hooks/xcp.py::compute_key come first, so a workspace can always "
    "override what the DLL says."
)
DLL_TIP = (
    "A DLL exporting any of GenerateKeyEx (the usual one for UDS),\n"
    "XCP_ComputeKeyFromSeed (XCP) or ASAP1A_CCP_ComputeKeyFromSeed\n"
    "(CCP). Each protocol uses its own first and the others after it;\n"
    "Check the DLL says which each will use."
)
PYTHON_TIP = (
    "A python.exe of the DLL's bitness, used to run the DLL when it\n"
    "cannot be loaded here. Left empty, pycangui looks for one itself\n"
    "through the py launcher, and only complains if there is none."
)
NOTHING = "No DLL chosen. UDS and XCP unlocking will use their hooks alone."
OTHER_BITS = (
    "This DLL is {kind} and pycangui is {host}-bit, so it cannot be loaded "
    "here. It will be run in {where} instead."
)
NO_HELPER = (
    "This DLL is {kind} and pycangui is {host}-bit, so it cannot be loaded "
    "here, and no {wanted}-bit Python was found to run it in. Install one "
    "and pycangui will find it, name one below, or rebuild the DLL as "
    "{host}-bit -- these are generally built Win32 only, so the project "
    "needs an x64 configuration adding."
)
FITS = "This DLL is {kind}, the same as pycangui, so it loads directly."
NOT_A_DLL = "This file does not look like a Windows DLL."
NOT_ONE = (
    "This DLL exports none of {names}, so it cannot answer a seed. It is "
    "probably not a seed and key DLL."
)
#: The protocols, as the check lists them.
PROTOCOLS = ((seedkey.UDS, "UDS"), (seedkey.XCP, "XCP"), (seedkey.CCP, "CCP"))


class SeedKeyDialog(QDialog):
    """Where the DLL is, and whether it can be used from here."""

    def __init__(self, ctx: Context, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(TITLE)
        self.setMinimumWidth(560)

        what = QLabel(WHAT)
        what.setWordWrap(True)

        self.dll = QLineEdit(str(ctx.settings.get(seedkey.DLL_KEY, "") or ""))
        self.dll.setPlaceholderText("none: the hooks decide on their own")
        self.dll.setToolTip(DLL_TIP)
        self.dll.textChanged.connect(lambda _t: self._look())
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.dll.clear)
        dll_row = QHBoxLayout()
        dll_row.addWidget(self.dll, 1)
        dll_row.addWidget(browse)
        dll_row.addWidget(clear)

        self.python = QLineEdit(str(ctx.settings.get(seedkey.PYTHON_KEY, "") or ""))
        self.python.setPlaceholderText("found automatically")
        self.python.setToolTip(PYTHON_TIP)
        self.python.textChanged.connect(lambda _t: self._look())
        find = QPushButton("Browse...")
        find.clicked.connect(self._browse_python)
        python_row = QHBoxLayout()
        python_row.addWidget(self.python, 1)
        python_row.addWidget(find)

        form = QFormLayout()
        form.addRow("DLL:", dll_row)
        form.addRow("Run it with:", python_row)

        #: What this DLL is and whether it can be used, worked out when the
        #: path changes rather than when somebody tries to unlock an ECU.
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.privileges = QLabel("")
        self.privileges.setWordWrap(True)
        self.privileges.setEnabled(False)

        test = QPushButton("Check the DLL")
        test.setToolTip(
            "Load it and see what it exports. XCP_ComputeKeyFromSeed is the\n"
            "one that matters, being what answers a seed;\n"
            "XCP_GetAvailablePrivileges is optional, and where it is there\n"
            "it is called to say which levels the DLL will unlock."
        )
        test.clicked.connect(self._test)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(what)
        layout.addLayout(form)
        layout.addWidget(self.verdict)
        layout.addWidget(test)
        layout.addWidget(self.privileges)
        layout.addWidget(box)
        self._look()

    # --- choosing it -----------------------------------------------------------------
    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, TITLE, self.dll.text(), "DLL (*.dll)")
        if path:
            self.dll.setText(path)

    def _browse_python(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Python to run the DLL", self.python.text(), "python.exe (python*.exe)"
        )
        if path:
            self.python.setText(path)

    # --- what it is ------------------------------------------------------------------
    def verdict_text(self) -> str:
        """The answer to "can this DLL be used from here", before trying."""
        path = self.dll.text().strip()
        if not path:
            return NOTHING
        if not Path(path).is_file():
            return f"No such file: {path}"
        kind = seedkey.machine(path)
        if not kind:
            return NOT_A_DLL
        host = seedkey.host_bits()
        if not seedkey.needs_another_python(path):
            return FITS.format(kind=kind)
        wanted = 32 if kind.startswith("32") else 64
        where = self.python.text().strip() or seedkey.find_python(wanted)
        if where:
            return OTHER_BITS.format(kind=kind, host=host, where=where)
        return NO_HELPER.format(kind=kind, host=host, wanted=wanted)

    def _look(self) -> None:
        self.verdict.setText(self.verdict_text())
        self.privileges.setText("")

    def _test(self) -> None:
        """Say which seed and key functions the DLL exports, which one each
        protocol will use -- marked where it is another protocol's -- and what
        the DLL will unlock, if it will say and can be asked from here.

        The functions are read from the file's export table, so this answers
        for a DLL of either bitness. Asking what it will unlock means calling
        it, which only a DLL of pycangui's own bitness allows.
        """
        path = self.dll.text().strip()
        if not path:
            self.privileges.setText(NOTHING)
            return
        try:
            found = seedkey.exports(path)
        except seedkey.SeedKeyError as exc:
            self.privileges.setText(str(exc))
            return
        uses = []
        for protocol, label in PROTOCOLS:
            if name := seedkey.used_by(found, protocol):
                fallback = " (fallback)" if seedkey.is_fallback(name, protocol) else ""
                uses.append(f"{label} uses {name}{fallback}")
        if not uses:
            names = ", ".join(seedkey.KEY_FUNCTIONS)
            self.privileges.setText(NOT_ONE.format(names=names))
            return
        exported = [name for name in seedkey.KNOWN if name in found]
        said = "Supported: " + ", ".join(exported) + "."
        if spare := seedkey.not_used(path):
            said += f" Also exported, but not used by pycangui: {', '.join(spare)}."
        said += " " + "; ".join(uses) + "."
        cannot = [label for protocol, label in PROTOCOLS if not seedkey.used_by(found, protocol)]
        if cannot:
            said += f" Nothing here for {' or '.join(cannot)}."
        if seedkey.PRIVILEGES_OF in found and not seedkey.needs_another_python(path):
            try:
                names = seedkey.names(seedkey.available_privileges(path))
            except seedkey.SeedKeyError as exc:
                said += f" Asked what it can unlock: {exc}"
            else:
                said += f" It says it can unlock: {names or 'nothing'}."
        self.privileges.setText(said)

    def save(self) -> None:
        self.ctx.settings.set(seedkey.DLL_KEY, self.dll.text().strip())
        self.ctx.settings.set(seedkey.PYTHON_KEY, self.python.text().strip())


def ask(ctx: Context, parent: QWidget | None = None) -> bool:
    """Run the dialog, keeping what was chosen. True if it was accepted."""
    dialog = SeedKeyDialog(ctx, parent)
    if dialog.exec() != QDialog.Accepted:
        return False
    dialog.save()
    return True
