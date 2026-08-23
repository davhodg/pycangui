"""Questions asked once a session, before doing something with consequences.

Three things pycangui does can disturb equipment that is not its own: joining
a live bus, transmitting onto one, and replaying a log onto one.  Each asks
before the first time, and then stays out of the way -- a dialog on every
send would be worse than useless, because it would be dismissed unread.

The unit of "once" is the *key*, which spells out what was agreed to.  Keying
the connect question on the bitrate rather than on the channel is deliberate:
saying yes to 500 kbit/s is not saying yes to 125 kbit/s on the same bus, and
the wrong bitrate is exactly the mistake the question is there to catch.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget


class Confirmations:
    """Remembers which questions have already been answered yes this session."""

    def __init__(self) -> None:
        self._agreed: set[str] = set()

    def agreed(self, key: str) -> bool:
        return key in self._agreed

    def ask(self, parent: QWidget | None, key: str, title: str, text: str) -> bool:
        """Ask, unless this exact key has already been agreed to.

        Defaults to Cancel: these dialogs appear in the middle of doing
        something else, and the safe answer should be the one you get by
        pressing return without reading carefully.
        """
        if key in self._agreed:
            return True
        answer = QMessageBox.warning(
            parent,
            title,
            text,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return False
        self._agreed.add(key)
        return True

    def allow(self, key: str) -> None:
        """Record agreement without asking, for a case that carries no risk."""
        self._agreed.add(key)

    def forget(self, key: str | None = None) -> None:
        if key is None:
            self._agreed.clear()
        else:
            self._agreed.discard(key)


def is_real(interface: str) -> bool:
    """Whether frames on this interface leave pycangui and reach real hardware."""
    return interface not in ("virtual", "")
