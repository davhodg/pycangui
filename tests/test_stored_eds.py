# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The EDS a node keeps in itself (0x1021), read, saved and used for the node."""

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen.manager import STOPPED, STORE_EDS, CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui import folders
from pycangui.ui.canopen_view import CanopenView

NODE = 5


def wait_until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, demo_device):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_stored_eds", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    yield manager, demo
    bus.disconnect_bus()
    manager.shutdown()


def listen(manager):
    got = {"read": [], "failed": [], "progress": []}
    manager.stored_eds.connect(lambda n, data, kind: got["read"].append((n, data, kind)))
    manager.stored_eds_failed.connect(lambda n, why: got["failed"].append((n, why)))
    manager.stored_eds_progress.connect(
        lambda n, done, total: got["progress"].append((done, total))
    )
    return got


def test_the_file_comes_back_as_the_device_holds_it(stack):
    manager, _demo = stack
    got = listen(manager)
    manager.read_stored_eds(NODE)  # with no EDS loaded for the node: that is the point
    wait_until(lambda: got["read"] or got["failed"])
    assert got["failed"] == []
    node, data, kind = got["read"][0]
    assert (node, kind) == (NODE, 0) and data == resources.path("demo.eds").read_bytes()
    assert got["progress"] and got["progress"][-1][0] <= len(data)


def test_a_node_that_keeps_none_says_so(stack):
    manager, demo = stack
    dictionary = demo["canopen_device"].state.device.object_dictionary
    del dictionary.indices[STORE_EDS]
    got = listen(manager)
    manager.read_stored_eds(NODE)
    wait_until(lambda: got["read"] or got["failed"])
    assert got["read"] == [] and got["failed"][0][0] == NODE


def test_reading_can_be_stopped(stack):
    manager, _demo = stack
    got = listen(manager)
    manager.read_stored_eds(NODE)
    manager.stop_reading_stored_eds()
    wait_until(lambda: got["read"] or got["failed"])
    assert got["read"] == [] and got["failed"] == [(NODE, STOPPED)]


# --- what the pane does with it -------------------------------------------------------------
@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    ctx = Context(log=print)
    manager = CanopenManager(BusManager())
    widget = CanopenView(manager, Hooks(ctx), ctx)
    widget.loaded = []
    monkeypatch.setattr(manager, "load_eds", lambda node, path: widget.loaded.append((node, path)))
    yield widget
    manager.shutdown()


def save_as(monkeypatch, path):
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: str(path))


def test_what_was_read_is_saved_and_used_for_the_node(view, tmp_path, monkeypatch):
    target = tmp_path / "read.eds"
    save_as(monkeypatch, target)
    text = resources.path("demo.eds").read_bytes()
    view._on_stored_eds(NODE, text + b"\xff" * 40, 0)  # padded to the size of its memory
    assert target.read_bytes() == text, "without the padding"
    assert view.loaded == [(NODE, str(target))]


def test_a_makers_own_format_is_kept_as_it_came_and_not_loaded(view, tmp_path, monkeypatch):
    target = tmp_path / "raw.bin"
    save_as(monkeypatch, target)
    view._on_stored_eds(NODE, b"\x1f\x8b\x08compressed", 128)
    assert target.read_bytes() == b"\x1f\x8b\x08compressed" and view.loaded == []


def test_something_that_is_not_an_eds_is_not_loaded(view, tmp_path, monkeypatch):
    target = tmp_path / "junk.eds"
    save_as(monkeypatch, target)
    view._on_stored_eds(NODE, b"\x00\x01\x02 not an eds at all", 0)
    assert target.is_file() and view.loaded == []


def test_nothing_is_saved_when_the_dialog_is_cancelled(view, tmp_path, monkeypatch):
    monkeypatch.setattr(folders, "save_file", lambda *a, **k: "")
    view._on_stored_eds(NODE, resources.path("demo.eds").read_bytes(), 0)
    assert view.loaded == [] and not list(Path(tmp_path).glob("*.eds"))


def test_an_empty_object_is_not_a_file(view, tmp_path, monkeypatch):
    save_as(monkeypatch, tmp_path / "empty.eds")
    view._on_stored_eds(NODE, b"\xff" * 64, 0)
    assert view.loaded == [] and not (tmp_path / "empty.eds").exists()


def test_the_node_has_the_action_beside_load_eds(view):
    offered = [a.text() for a in view.node_menu(None).actions()]
    assert offered.index("Read EDS from node") == offered.index("Load EDS...") + 1
