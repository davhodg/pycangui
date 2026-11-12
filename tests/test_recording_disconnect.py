# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Disconnecting the last channel while recording asks first."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from pycangui.ui import recording_question
from pycangui.ui.main_window import MainWindow


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.recorder.stop()
    win.close()


def recording(window, tmp_path, monkeypatch, answer):
    """The selected channel connected and recorded, and the question answered."""
    bus = window.channels.active_bus()
    bus.connect_bus("virtual", f"vcan_rec_{answer}", 500000, False)
    window.connect_bar.set_connected(True)
    assert window.recorder.start(tmp_path / "capture.asc")
    asked = []
    monkeypatch.setattr(
        recording_question, "ask", lambda _parent, path: asked.append(path) or answer
    )
    return bus, asked


def test_cancel_leaves_it_connected_and_recording(app, window, tmp_path, monkeypatch):
    bus, asked = recording(window, tmp_path, monkeypatch, recording_question.CANCEL)
    window.connect_bar.button.setChecked(False)  # Disconnect pressed
    assert asked
    assert bus.is_connected and window.recorder.is_recording
    assert window.connect_bar.button.isChecked(), "the button is put back"


def test_stop_disconnects_and_stops_recording(app, window, tmp_path, monkeypatch):
    bus, _asked = recording(window, tmp_path, monkeypatch, recording_question.STOP)
    window.connect_bar.button.setChecked(False)
    assert not bus.is_connected and not window.recorder.is_recording


def test_keep_disconnects_and_goes_on_recording(app, window, tmp_path, monkeypatch):
    bus, _asked = recording(window, tmp_path, monkeypatch, recording_question.KEEP)
    window.connect_bar.button.setChecked(False)
    assert not bus.is_connected and window.recorder.is_recording


def test_not_asked_while_another_channel_is_still_connected(app, window, tmp_path, monkeypatch):
    other = window.channels.add("CAN 2")
    other.connect_bus("virtual", "vcan_rec_other", 500000, False)
    bus, asked = recording(window, tmp_path, monkeypatch, recording_question.CANCEL)
    window.connect_bar.button.setChecked(False)
    assert not asked
    assert not bus.is_connected and window.recorder.is_recording
    other.disconnect_bus()


def test_not_asked_when_not_recording(app, window, monkeypatch):
    bus = window.channels.active_bus()
    bus.connect_bus("virtual", "vcan_rec_none", 500000, False)
    window.connect_bar.set_connected(True)
    asked = []
    monkeypatch.setattr(recording_question, "ask", lambda *a: asked.append(a))
    window.connect_bar.button.setChecked(False)
    assert not asked and not bus.is_connected
