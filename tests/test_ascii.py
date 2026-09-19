# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Reading a CAN id as text: one pane, one id.

It used to be one pane with a tab per id and a hand-rolled pop-out button.
That was the right shape when a dock could only ever be one of a kind; two
streams side by side is what people want, and tabs are precisely the thing that
forbids it. So the tabs are gone, and what is left has to hold: each pane
reads its own id, remembers it, and says which one it is showing.
"""

import json

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import workspaces
from pycangui.core.bus import Frame
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.ascii_view import AsciiView, Stream, decode
from pycangui.ui.main_window import MainWindow


def frame(can_id, data, extended=False, error=False):
    return Frame(0.0, "vcan", can_id, extended, False, True, data, error=error)


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    return AsciiView(Channels(), Context(log=print), Stream(can_id=0x123))


# --- decoding ---------------------------------------------------------------------------
def test_the_printable_characters_come_through():
    assert decode(b"Hello!") == "Hello!"


def test_padding_is_dropped_rather_than_shown():
    """A frame is a fixed length and its tail is padding far more often than
    it is data."""
    assert decode(b"Hi\x00\x00\x00\x00\x00\x00") == "Hi"


def test_a_device_that_ends_lines_with_crlf_does_not_double_space():
    assert decode(b"one\r\ntwo\r\n") == "one\ntwo\n"


def test_the_layout_the_device_meant_is_kept():
    assert decode(b"a\tb\nc") == "a\tb\nc"


def test_anything_else_becomes_a_dot():
    """A stream of dots is how you find out the id is wrong."""
    assert decode(b"\x01\x02ok") == "..ok"


def test_leading_bytes_can_be_skipped():
    """Devices often put a length or a sequence number at the front."""
    assert decode(b"\x04text", skip=1) == "text"


# --- one pane, one id ----------------------------------------------------------------------
def test_the_text_of_its_id_arrives(view):
    view.on_frames([frame(0x123, b"hello")])
    assert view.text.toPlainText() == "hello"


def test_another_id_is_not_its_business(view):
    view.on_frames([frame(0x124, b"nope")])
    assert view.text.toPlainText() == ""


def test_the_same_number_with_29_bits_is_a_different_id(view):
    view.on_frames([frame(0x123, b"eleven"), frame(0x123, b"twentynine", extended=True)])
    assert view.text.toPlainText() == "eleven"


def test_an_error_frame_is_not_text(view):
    """Its identifier carries error flags, not an identifier."""
    view.on_frames([frame(0x123, b"junk", error=True)])
    assert view.text.toPlainText() == ""


def test_a_pane_with_no_id_yet_reads_nothing(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    empty = AsciiView(Channels(), Context(log=print))
    empty.on_frames([frame(0x123, b"hello")])
    assert empty.text.toPlainText() == ""
    assert empty.stream.pane_title == "ASCII Log"


def test_the_skip_reaches_the_text(view):
    view.skip.setValue(2)
    view.on_frames([frame(0x123, b"\x00\x05text")])
    assert view.text.toPlainText() == "text"


def test_pointing_it_at_another_id_is_announced(view):
    seen = []
    view.changed.connect(seen.append)
    view.id_edit.setText("456")
    view.id_edit.editingFinished.emit()

    assert view.stream.can_id == 0x456
    assert seen and seen[-1].can_id == 0x456


def test_a_bad_id_says_so_and_keeps_reading_the_old_one(view):
    said = []
    view.ctx.warn = said.append
    view.id_edit.setText("zzz")
    view.id_edit.editingFinished.emit()

    assert said and "not a hex id" in said[0]
    assert view.stream.can_id == 0x123, "still reading what it was reading"
    assert view.id_edit.text() == "123", "and the box says so"


def test_a_pane_says_which_id_it_is_showing():
    assert Stream(can_id=0x77F, name="Node 5").pane_title == "ASCII 77F  Node 5"
    assert Stream(can_id=0x1ABCDEF, extended=True).pane_title == "ASCII 01ABCDEF"
    assert Stream().pane_title == "ASCII Log"


# --- as panes -------------------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    settle(app)
    yield win
    win.close()


def test_two_ids_can_be_read_side_by_side(app, window):
    """The arrangement tabs forbade, which is why they went."""
    second = window.panes.add("ascii")
    settle(app)
    window.panes.view("ascii").stream = Stream(can_id=0x123)
    window.panes.view(second).stream = Stream(can_id=0x456)

    window.channels.frames.emit([frame(0x123, b"one"), frame(0x456, b"two")])
    settle(app)
    assert window.panes.view("ascii").text.toPlainText() == "one"
    assert window.panes.view(second).text.toPlainText() == "two"


def test_the_dock_says_which_id_it_is_showing(app, window):
    view = window.panes.view("ascii")
    view.id_edit.setText("77F")
    view.id_edit.editingFinished.emit()
    settle(app)
    assert window.panes.docks["ascii"].windowTitle() == "ASCII 77F"


def test_a_name_of_your_own_survives_pointing_it_somewhere_else(app, window):
    """Renaming a pane is a statement about the pane, not about the id."""
    window.panes.rename("ascii", "Motor console")
    view = window.panes.view("ascii")
    view.id_edit.setText("77F")
    view.id_edit.editingFinished.emit()
    settle(app)
    assert window.panes.docks["ascii"].windowTitle() == "Motor console"


def test_the_id_comes_back_next_time(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    first = MainWindow()
    first.show()
    settle(app)
    view = first.panes.view("ascii")
    view.id_edit.setText("77F")
    view.id_edit.editingFinished.emit()
    settle(app)
    first.close()
    settle(app)

    again = MainWindow()
    again.show()
    settle(app)
    assert again.panes.view("ascii").stream.can_id == 0x77F
    assert again.panes.docks["ascii"].windowTitle() == "ASCII 77F"
    again.close()


def test_a_removed_pane_stops_reading(app, window):
    """Deletion is deferred, so without disconnecting it goes on filling up."""
    second = window.panes.add("ascii")
    view = window.panes.view(second)
    view.stream = Stream(can_id=0x123)
    window.panes.remove(second)

    window.channels.frames.emit([frame(0x123, b"hello")])
    settle(app)
    assert view.text.toPlainText() == ""


# --- and the tabs that used to hold them ------------------------------------------------------
def older_setup(tmp_path, streams):
    workspace = tmp_path / "workspaces" / "default"
    workspace.mkdir(parents=True)
    (workspace / "settings.json").write_text(
        json.dumps({"ascii.streams": streams}), encoding="utf-8"
    )


def test_the_streams_the_tabbed_pane_kept_become_panes(app, tmp_path, monkeypatch):
    """Somebody who had two ids being read should find two panes, not an empty
    one and a lost list."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    older_setup(
        tmp_path,
        [
            {"id": 0x77F, "extended": False, "name": "Node 5", "skip": 0},
            {"id": 0x780, "extended": False, "name": "", "skip": 1},
        ],
    )

    window = MainWindow()
    window.show()
    settle(app)
    panes = [n for n in window.panes.names() if n.startswith("ascii")]
    assert panes == ["ascii", "ascii 2"]
    assert [window.panes.view(n).stream.can_id for n in panes] == [0x77F, 0x780]
    assert window.panes.view("ascii 2").stream.skip == 1
    assert window.ctx.settings.get("ascii.streams") is None, "moved, not copied"
    window.close()


def test_they_are_still_there_after_that(app, tmp_path, monkeypatch):
    """The migration runs once; what it made has to persist on its own."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    older_setup(tmp_path, [{"id": 0x77F}, {"id": 0x780}])
    first = MainWindow()
    first.show()
    settle(app)
    first.close()
    settle(app)

    again = MainWindow()
    again.show()
    settle(app)
    panes = [n for n in again.panes.names() if n.startswith("ascii")]
    assert [again.panes.view(n).stream.can_id for n in panes] == [0x77F, 0x780]
    again.close()


# --- what the id says, and asking the device to print -----------------------------------
def test_29_bit_is_read_off_the_id_that_was_typed(app, tmp_path, monkeypatch):
    """A tick box asked a question the id mostly answers: only an id of
    0x7FF or less is ambiguous, and writing it in full settles that."""
    from pycangui.ui.ascii_view import _is_extended

    assert not _is_extended("185", 0x185), "three digits is an 11-bit id"
    assert _is_extended("18FEF100", 0x18FEF100), "and this can only be 29-bit"
    assert _is_extended("00000185", 0x185), "written out in full: 29-bit, said deliberately"


def test_the_pane_reads_the_id_the_way_it_was_typed(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = AsciiView(Channels(), ctx)

    view.id_edit.setText("00000185")
    view.id_edit.editingFinished.emit()
    assert view.stream.extended and view.stream.can_id == 0x185

    view.id_edit.setText("185")
    view.id_edit.editingFinished.emit()
    assert not view.stream.extended


def test_enable_asks_the_hook_and_says_when_there_is_none(app, tmp_path, monkeypatch):
    """Plenty of devices print nothing until asked, and how to ask is the
    maker's business -- so a button that looked as though it worked would
    send somebody looking at the wiring."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    said: list[str] = []
    ctx = Context(log=said.append)
    hooks = Hooks(ctx)
    view = AsciiView(Channels(), ctx, Stream(can_id=0x300), hooks)

    view.enable.setChecked(True)

    assert not view.enable.isChecked(), "it does not pretend it worked"
    assert any("hooks/ascii_log.py" in line for line in said), "and says what to write"


def test_a_hook_that_sends_the_command_keeps_the_button_down(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    said: list[str] = []
    ctx = Context(log=said.append)
    hooks = Hooks(ctx)
    hook = (
        "def enable(on, can_id, extended, *, ctx):\n"
        "    ctx.log(f'asked {on} {can_id:X}')\n"
        "    return True\n"
    )
    (workspaces.hooks_dir() / "ascii_log.py").write_text(hook)
    hooks.reload()
    view = AsciiView(Channels(), ctx, Stream(can_id=0x300), hooks)

    view.enable.setChecked(True)

    assert view.enable.isChecked() and view.enable.text() == "Disable"
    assert any("asked True 300" in line for line in said)
