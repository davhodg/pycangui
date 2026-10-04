# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The top row shows the rate a channel is running at, after a UDS baud change."""

import pytest
from PySide6.QtCore import QSettings

from pycangui.ui.main_window import MainWindow


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


def test_the_running_rate_is_shown_and_the_own_one_kept(app, window):
    """The greyed box said 500 kbit/s while the channel ran at 250, after the
    ECUs had been moved."""
    bar = window.connect_bar
    name = bar.selector.currentText()
    bus = window.channels.get(name)
    bus.connect_bus("virtual", "vcan_running_rate", 500_000, False)
    app.processEvents()
    assert bar.bitrate.currentData() == 500_000

    bus.reconnect_at(250_000)  # what following a baud rate change does
    app.processEvents()
    assert bar.bitrate.currentData() == 250_000, "the rate in use"
    saved = window.ctx.settings.get(f"channels.{name}", {})
    assert saved.get("bitrate", 500_000) == 500_000, "and not saved as the channel's own"

    bus.disconnect_bus()
    app.processEvents()
    assert bar.bitrate.currentData() == 500_000, "its own again, for the next connect"
