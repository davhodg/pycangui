# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Read RPDO config: how CAN Transmit learns a remapped node's RPDOs.

The manager could read them and CAN Transmit told people to press the button,
but the button itself was never put in the pane -- so the one way to offer a
remapped node's RPDOs was a control that did not exist.
"""

import pytest

from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.canopen_view import CanopenView


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    bus = BusManager()
    widget = CanopenView(CanopenManager(bus), Hooks(ctx), ctx)
    yield widget
    bus.disconnect_bus()


def test_the_button_is_in_the_pane(view):
    assert view.read_rpdos_btn.text() == "Read RPDO config"
    assert view.read_rpdos_btn.isVisibleTo(view), "a button nobody can reach is no button"
    assert "CAN Transmit" in view.read_rpdos_btn.toolTip()


def test_it_reads_the_selected_node(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view.manager, "read_rpdo_config", asked.append)
    monkeypatch.setattr(view, "selected_node", lambda: 5)
    view.read_rpdos_btn.click()
    assert asked == [5]


def test_without_a_node_selected_it_does_nothing(view, monkeypatch):
    asked = []
    monkeypatch.setattr(view.manager, "read_rpdo_config", asked.append)
    monkeypatch.setattr(view, "selected_node", lambda: None)
    view.read_rpdos_btn.click()
    assert asked == []


def test_what_transmit_says_to_press_is_what_is_there(view):
    """The RPDO picker's empty list names the button; the two must agree."""
    from pathlib import Path

    import pycangui.ui.tx_view as tx_view

    text = Path(tx_view.__file__).read_text(encoding="utf-8")
    assert f"'{view.read_rpdos_btn.text()}'" in text
