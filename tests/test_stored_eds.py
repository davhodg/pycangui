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
from pycangui.ui import folders, messages
from pycangui.ui.canopen_view import CanopenView

NODE = 5


def wait_until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
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
    boxes = []
    monkeypatch.setattr(messages, "warning", lambda *a, **k: boxes.append(a[1]))
    view._on_stored_eds(NODE, b"\xff" * 64, 0)
    assert view.loaded == [] and not (tmp_path / "empty.eds").exists()
    assert boxes, "said where the button was pressed"


def test_the_node_has_the_action_beside_load_eds(view):
    offered = [a.text() for a in view.node_menu(None).actions()]
    assert offered.index("Read EDS from node") == offered.index("Load EDS...") + 1


# --- a DOMAIN read in the object dictionary says how far it has got --------------------------
def test_a_domain_read_says_how_far_it_has_got(stack):
    manager, _demo = stack
    manager.load_eds(NODE, str(resources.path("demo.eds")))
    wait_until(lambda: manager.node(NODE) is not None and len(manager.node(NODE).object_dictionary))
    progress, results = [], []
    manager.sdo_progress.connect(lambda n, i, s, done, total: progress.append((i, done, total)))
    manager.sdo_result.connect(lambda n, i, s, value, error: results.append((i, value, error)))

    manager.sdo_read(NODE, STORE_EDS, 0)
    wait_until(lambda: results)
    _index, value, error = results[0]
    assert error is None and value == resources.path("demo.eds").read_bytes()
    assert progress[0] == (STORE_EDS, 0, 0), "said at the start, before a byte has come"
    assert len(progress) > 2 and progress[-1][1] <= len(value), "and as it went"

    results.clear()
    manager.sdo_read(NODE, 0x2001, 0)
    wait_until(lambda: results)
    assert [p for p in progress if p[0] == 0x2001] == [], "an ordinary object is just read"


def test_the_cell_reads_reading_until_the_block_has_come():
    from pycangui.ui.canopen_view import reading_text

    shown = {reading_text(0, 0), reading_text(700, 10_240), reading_text(700, 0)}
    assert len(shown) == 3, "starting, a share of a known size, and bytes of an unknown one"
