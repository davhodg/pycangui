# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A cancelled EDS question is kept with the workspace, so a device with no EDS
-- a bootloader's second processor, say -- is not asked about at every start,
and least of all in the middle of a flash."""

import pytest
from PySide6.QtCore import QSettings

from pycangui.canopen import NodeIdentity
from pycangui.ui import canopen_view

DEVICE = NodeIdentity(node_id=9, vendor_id=0x1234, product_code=0x56, revision=1)


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    made = MainWindow()
    yield made
    made.close()


@pytest.fixture
def dialogs(monkeypatch):
    """Every file dialog the pane opens, answered as ``dialogs.answer`` says."""
    opened = []

    def open_file(*args, **kwargs):
        opened.append(args)
        return dialogs.answer

    dialogs.answer = ""  # Cancel
    dialogs.opened = opened
    monkeypatch.setattr(canopen_view.folders, "open_file", open_file)
    return dialogs


def started_again(view):
    """What a new run of pycangui forgets: who was asked this time."""
    view._asked.clear()


def test_a_cancel_is_not_asked_again_after_a_restart(window, dialogs):
    view = window.canopen_view
    assert view._ask_for_eds(DEVICE) is None
    assert len(dialogs.opened) == 1
    started_again(view)
    assert view._ask_for_eds(DEVICE) is None
    assert len(dialogs.opened) == 1, "not asked a second time"
    assert DEVICE.key in window.ctx.settings.get(canopen_view.EDS_DECLINED_KEY), "the workspace's"


def test_loading_an_eds_by_hand_forgets_the_cancel(window, dialogs, monkeypatch, tmp_path):
    view = window.canopen_view
    view._ask_for_eds(DEVICE)
    view.on_node_seen(DEVICE.node_id, "found by scan")
    view.nodes.setCurrentItem(view._node_item(DEVICE.node_id))
    view._identities[DEVICE.node_id] = DEVICE
    loaded = []
    monkeypatch.setattr(view.manager, "load_eds", lambda n, path: loaded.append((n, path)))
    dialogs.answer = str(tmp_path / "device.eds")
    view._load_eds_clicked()
    assert loaded and DEVICE.key not in window.ctx.settings.get(canopen_view.EDS_DECLINED_KEY)
    started_again(view)
    dialogs.answer = ""
    view._ask_for_eds(DEVICE)
    assert len(dialogs.opened) == 3, "asked again, once loading one by hand undid the cancel"


def test_a_device_with_no_identity_is_not_kept(window, dialogs):
    """A cancel for one with no vendor would cover every device with none."""
    view = window.canopen_view
    nobody = NodeIdentity(node_id=3)
    view._ask_for_eds(nobody)
    assert not window.ctx.settings.get(canopen_view.EDS_DECLINED_KEY)
    started_again(view)
    view._ask_for_eds(nobody)
    assert len(dialogs.opened) == 2
