# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Help > Diagnostics: a window of its own, and what pycangui is sending.

A frame on the bus that nothing on screen accounts for was impossible to pin
on pycangui or on a device from inside the window: the report said nothing
about what was being sent. It lists every cyclic message now, whose it is
and who keeps its time, with the protocols' state, and opens in a window
rather than pushing a hundred lines through the Event Log.
"""

import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication

from pycangui.ui import diagnostics as report
from pycangui.ui.help_menu import diagnostics
from pycangui.ui.main_window import MainWindow
from pycangui.ui.tx_view import COL_CYCLIC


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.channels.active_bus().connect_bus("virtual", "vcan_diagnostics", 500_000, False)
    app.processEvents()
    yield win
    win.close()


def pump(app, seconds=0.3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def section_of(text: str, title: str) -> str:
    """The lines of one section, its heading excluded."""
    lines = text.splitlines()
    start = lines.index(title) + 1
    body = []
    for line in lines[start:]:
        if not line.startswith("  "):
            break
        body.append(line)
    return "\n".join(body)


def test_every_cyclic_message_is_listed_with_whose_it_is(app, window):
    tx = window._transmit_panes()[0]
    row = tx.add_message({"kind": "raw", "id": "123", "data": "01 02", "period": 50})
    tx.item(row).setCheckState(COL_CYCLIC, Qt.Checked)
    window.canopen_view.sync_btn.setChecked(True)
    pump(app)
    sending = section_of(diagnostics(window), "Sending")
    assert "2 cyclic message(s)" in sending
    assert "0x123  01 02  every 50 ms, timed by pycangui -- CAN Transmit" in sending
    assert "0x080" in sending and "CANopen SYNC" in sending
    assert "sent" in sending and "0 received" in sending, "and what went out lately"


def test_a_pause_and_the_protocols_are_reported(app, window):
    window.ctx.pause.hold("UDS transfer")
    text = diagnostics(window)
    assert "held by UDS transfer" in section_of(text, "Automatic traffic")
    assert "node ids heard: 1-127" in section_of(text, "CANopen")
    for title in ("UDS", "J1939", "XCP / CCP", "Loaded", "Threads"):
        assert section_of(text, title), f"{title} says something, if only nothing"
    window.ctx.pause.release("UDS transfer")


def test_the_demo_device_is_said_to_be_off_when_it_is(window):
    """It read 'running' whatever: the list it was tested against is never None."""
    assert "demo device=off" in diagnostics(window)


def test_recent_problems_are_in_it(app, window):
    window.events.warning("node 5 did not answer")
    app.processEvents()
    assert "warning: node 5 did not answer" in diagnostics(window)


def test_a_section_that_fails_costs_only_itself(window, monkeypatch):
    def broken(_window):
        raise RuntimeError("odd state")

    monkeypatch.setattr(report, "SECTIONS", (("Broken", broken), *report.SECTIONS[1:]))
    text = diagnostics(window)
    assert "could not be read: RuntimeError: odd state" in text
    assert section_of(text, "Threads"), "the rest is still there"


def test_it_opens_in_a_window_of_its_own_and_is_copied(app, window, tmp_path, monkeypatch):
    from pycangui.ui import folders

    window.help_menu._show_diagnostics()
    dialog = window.help_menu.diagnostics
    assert dialog.isVisible() and "Sending" in dialog.report()
    assert QGuiApplication.clipboard().text() == dialog.report(), "as the bug report form says"
    assert "Sending" not in window.log.toPlainText(), "not pushed through the Event Log"

    window.help_menu._show_diagnostics()
    assert window.help_menu.diagnostics is dialog, "one window, brought forward"

    path = tmp_path / "report.txt"
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(path))
    dialog.save()
    assert "Sending" in path.read_text(encoding="utf-8")
    dialog.close()
