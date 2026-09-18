# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What the user has agreed to before pycangui disturbs equipment.

Two shapes of the same subject.

**Once, before anything, every time.**  A notice at start-up saying what the
tool is capable of, which has to be clicked through and cannot be switched off.
It is not a question about anything in particular, which is why it is the one
dialog here with no "do not ask again" on it: a notice dismissed for good on
the first afternoon is never seen again on that login -- not months later, and
not by anyone else sharing the account, as a bench computer often is. (Another
login has settings of its own, so it sees the notice regardless.)  It is also
where the slow half of starting up hides -- the libraries load behind it, so
the notice costs no time at all.

**Once a session, per thing.**  Three things pycangui does can disturb
equipment that is not its own: joining a live bus, transmitting onto one, and
replaying a log onto one. Each asks before the first time, and then stays out
of the way -- a dialog on every send would be worse than useless, because it
would be dismissed unread.

The unit of "once" is the *key*, which spells out what was agreed to. Keying
the connect question on the bitrate rather than on the channel is deliberate:
saying yes to 500 kbit/s is not saying yes to 125 kbit/s on the same bus, and
the wrong bitrate is exactly the mistake the question is there to catch.

Both offer to be remembered, and what "remembered" means is the interesting
part. An answer is kept **against the person who gave it, on the machine they
gave it on**, in ``QSettings`` -- never in the workspace. A workspace is a
folder made to be copied, backed up and handed to someone else, and an agreement
that travelled inside one would mean somebody else's window, on somebody else's
bench, quietly not asking. The user name is stored alongside and checked, so
that a settings store which does somehow arrive on another machine, or under
another account, asks that person for themselves.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget

#: Kept outside every workspace, deliberately. See the module docstring.
AGREED_SETTING = "confirmations/agreed"
USER_SETTING = "confirmations/user"

REMEMBER_LABEL = "Do not ask me this again on this machine"
REMEMBER_TIP = (
    "Kept for you, on this computer, outside any workspace -- so it does not\n"
    "travel in a workspace handed to somebody else, and another account on\n"
    "this machine is asked for itself.\n\n"
    "Tools > Ask about everything again brings the questions back."
)

NOTICE_TITLE = "Before you start"
#: Says what the tool can do and what to do about that, and promises nothing
#: about what it will not do. A promise in a safety notice is one a fault can
#: break, and the person who read it has stopped checking for themselves -- so
#: the questions pycangui asks are described as what they are, a reminder.
NOTICE = (
    "pycangui talks to real equipment. It is intended only for people trained and "
    "experienced in working with CAN networks and the equipment connected to them.\n\n"
    "Joining a bus at the wrong bitrate makes a controller signal an error on every "
    "frame it sees, and those error frames go out on the wire -- they can drive the "
    "nodes that are working off the bus. Transmitting, replaying a log, writing "
    "parameters, enabling a drive and downloading firmware all change what equipment "
    "does, and not all of them can be undone.\n\n"
    "Know what is on the bus before you join it, and what a device will do before you "
    "write to it. The questions pycangui asks along the way are a reminder, not a "
    "safeguard: like any software it can have faults, so do not rely on it to keep "
    "anything off the bus. Where a mistake could hurt someone or damage equipment, "
    "keep a way to stop that equipment within reach.\n\n"
    "Provided under the Apache License 2.0, without warranty of any kind."
)


def _who() -> str:
    """The account this is being agreed by, or "" where that cannot be told."""
    try:
        return getpass.getuser()
    except Exception:  # no password database, no USERNAME: not worth failing over
        return ""


class Remembered:
    """Agreements kept between sessions, for one person on one machine.

    A thin wrapper over ``QSettings`` rather than a store of our own, because
    ``QSettings`` is already per user by construction -- the registry under
    HKEY_CURRENT_USER on Windows, the account's own config directory
    elsewhere -- which is most of what is wanted here. The user name is
    written alongside and checked on the way out, so that the one case
    ``QSettings`` does not cover, a store copied somewhere else, is covered
    too: it asks that person for themselves rather than assuming the answer
    somebody else gave.
    """

    def __init__(self, settings: QSettings | None = None, user: str | None = None) -> None:
        self._settings = settings if settings is not None else QSettings()
        self._user = _who() if user is None else user

    def keys(self) -> set[str]:
        if self._settings.value(USER_SETTING, "") != self._user:
            return set()  # somebody else's answers, which are not ours to use
        stored = self._settings.value(AGREED_SETTING, [])
        if isinstance(stored, str):  # a one-item list comes back as a string
            stored = [stored]
        return {str(key) for key in stored or []}

    def add(self, key: str) -> None:
        self._settings.setValue(USER_SETTING, self._user)
        self._settings.setValue(AGREED_SETTING, sorted(self.keys() | {key}))

    def clear(self) -> int:
        """Forget everything, so every question comes back. Returns how many."""
        how_many = len(self.keys())
        for name in (AGREED_SETTING, USER_SETTING):
            self._settings.remove(name)
        return how_many


def accept_notice(
    parent: QWidget | None = None, while_shown: Callable[[], None] | None = None
) -> bool:
    """Show the start-up notice. False means the user chose not to go on.

    Shown before the window is built rather than over the top of it, so that
    nothing -- not a startup hook, not a workspace reopening its channels --
    can have touched a bus before it has been read.

    **Every time, with no way to switch it off**, which is the one place in
    pycangui where a dialog is not offered a tick box. The per-action
    questions are answered once because they are about a thing you are doing
    on purpose; this is not a question at all. A notice dismissed for good on
    the first afternoon is never seen again on that login, including by anyone
    sharing the account, and it costs one keypress a session.

    ``while_shown`` is called once the notice is on screen and before the
    answer is waited for. That is where the slow half of starting up goes: a
    second of libraries loads behind a dialog somebody is reading, instead of
    a second of nothing before one appears. The safety notice pays for itself
    twice.
    """
    box = QMessageBox(
        QMessageBox.Warning,
        NOTICE_TITLE,
        NOTICE_TITLE,
        QMessageBox.Ok | QMessageBox.Cancel,
        parent,
    )
    box.setInformativeText(NOTICE)  # see Confirmations.ask for why not the title alone
    box.button(QMessageBox.Ok).setText("Continue")
    box.button(QMessageBox.Cancel).setText("Quit")
    box.setDefaultButton(QMessageBox.Ok)
    if while_shown is not None:
        # Painted first, then the slow work: the point is that something is on
        # screen while it happens. Done here rather than on a timer inside
        # exec() so that it has demonstrably run by the time anybody answers.
        box.show()
        QApplication.processEvents()
        while_shown()
    return box.exec() == QMessageBox.Ok


class Confirmations:
    """Which questions have been answered yes, this session or for good.

    ``remembered`` is what makes an answer outlive the session, and it is
    optional so that anything constructing one of these for a test, or for a
    pane of its own, gets the session-only behaviour rather than reaching into
    the real user's settings by accident.
    """

    def __init__(self, remembered: Remembered | None = None) -> None:
        self._store = remembered
        self._agreed: set[str] = set(remembered.keys()) if remembered is not None else set()

    def agreed(self, key: str) -> bool:
        return key in self._agreed

    def ask(self, parent: QWidget | None, key: str, title: str, text: str) -> bool:
        """Ask, unless this exact key has already been agreed to.

        Defaults to Cancel: these dialogs appear in the middle of doing
        something else, and the safe answer should be the one you get by
        pressing return without reading carefully.

        The tick box is offered only where there is somewhere to keep the
        answer. Offering it and then forgetting at the end of the session
        would be a promise the dialog could not keep.

        The question is the dialog's text, not only its title. macOS does not
        show a message box's title at all, so a question kept there would leave
        Yes and Cancel under an explanation of something nobody had asked.
        """
        if key in self._agreed:
            return True
        box = QMessageBox(
            QMessageBox.Warning, title, title, QMessageBox.Yes | QMessageBox.Cancel, parent
        )
        box.setInformativeText(text)
        box.setDefaultButton(QMessageBox.Cancel)
        again = None
        if self._store is not None:
            again = QCheckBox(REMEMBER_LABEL)
            again.setToolTip(REMEMBER_TIP)
            box.setCheckBox(again)
        if box.exec() != QMessageBox.Yes:
            return False
        self._agreed.add(key)
        if again is not None and again.isChecked():
            self._store.add(key)
        return True

    def allow(self, key: str) -> None:
        """Record agreement without asking, for a case that carries no risk."""
        self._agreed.add(key)

    def forget(self, key: str | None = None) -> None:
        """Ask again this session. What was remembered for good is untouched."""
        if key is None:
            self._agreed.clear()
        else:
            self._agreed.discard(key)

    def forget_everything(self) -> int:
        """Ask again, including the answers that were being kept for good."""
        self._agreed.clear()
        return self._store.clear() if self._store is not None else 0


def is_real(interface: str) -> bool:
    """Whether frames on this interface leave pycangui and reach real hardware."""
    return interface not in ("virtual", "")
