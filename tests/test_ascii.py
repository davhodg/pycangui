"""Reading a CAN id as text, several at once, each able to have its own window."""

import pytest
from PySide6.QtCore import QSettings

from pycangui.core.bus import Frame
from pycangui.core.channels import Channels
from pycangui.core.context import Context
from pycangui.ui.ascii_view import AsciiView, Stream, decode


def frame(can_id, data, extended=False, error=False):
    return Frame(0.0, "vcan", can_id, extended, False, True, data, error=error)


@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    widget = AsciiView(Channels(), Context(log=print))
    yield widget
    widget.shutdown()


# --- decoding ---------------------------------------------------------------------------
def test_the_printable_characters_come_through():
    assert decode(b"Hello!") == "Hello!"


def test_padding_is_dropped_rather_than_shown():
    """A frame is a fixed length and its tail is padding far more often than data."""
    assert decode(b"Hi\x00\x00\x00\x00\x00\x00") == "Hi"


def test_a_device_that_ends_lines_with_crlf_does_not_double_space():
    assert decode(b"one\r\ntwo\r\n") == "one\ntwo\n"


def test_the_layout_the_device_meant_is_kept():
    assert decode(b"a\tb\nc") == "a\tb\nc"


def test_anything_else_becomes_a_dot():
    """A stream of dots is how you find out the id is wrong."""
    assert decode(bytes([0x01, 0x02, 0xFF])) == "..."


def test_leading_bytes_can_be_skipped():
    """Devices often put a length or a sequence number at the front."""
    assert decode(b"\x05hello", skip=1) == "hello"


# --- streams ----------------------------------------------------------------------------
def test_an_id_can_be_watched_and_its_text_arrives(view):
    assert view.add_stream(Stream(0x123, name="ECU"))
    view._on_frames([frame(0x123, b"Ready\n")])
    assert view._texts["123"].toPlainText() == "Ready\n"


def test_several_ids_are_read_at_once_each_in_its_own_tab(view):
    view.add_stream(Stream(0x100, name="one"))
    view.add_stream(Stream(0x200, name="two"))
    view._on_frames([frame(0x100, b"A"), frame(0x200, b"B"), frame(0x100, b"C")])

    assert view.tabs.count() == 2
    assert view._texts["100"].toPlainText() == "AC"
    assert view._texts["200"].toPlainText() == "B"
    assert view.tabs.tabText(0).startswith("100")


def test_the_same_number_with_29_bits_is_a_different_id(view):
    view.add_stream(Stream(0x123))
    view.add_stream(Stream(0x123, extended=True))
    assert view.tabs.count() == 2

    view._on_frames([frame(0x123, b"std"), frame(0x123, b"ext", extended=True)])
    assert view._texts["123"].toPlainText() == "std"
    assert view._texts["123x"].toPlainText() == "ext"


def test_an_error_frame_is_not_text(view):
    """Its id carries error flags rather than an identifier."""
    view.add_stream(Stream(0x123))
    view._on_frames([frame(0x123, b"junk", error=True)])
    assert view._texts["123"].toPlainText() == ""


def test_the_same_id_is_not_added_twice(view):
    assert view.add_stream(Stream(0x123))
    assert not view.add_stream(Stream(0x123, name="again"))
    assert view.tabs.count() == 1


def test_a_bad_id_says_so_rather_than_doing_nothing(view):
    view.id_edit.setText("nonsense")
    view._add_from_controls()
    assert view.tabs.count() == 0


def test_closing_a_tab_stops_reading_that_id(view):
    view.add_stream(Stream(0x123))
    view._close_tab(0)
    assert view.tabs.count() == 0
    view._on_frames([frame(0x123, b"gone")])  # must not raise


def test_the_skip_reaches_the_text(view):
    view.add_stream(Stream(0x123, skip=2))
    view._on_frames([frame(0x123, b"\x01\x07hello")])
    assert view._texts["123"].toPlainText() == "hello"


# --- a window of its own ------------------------------------------------------------------
def test_a_stream_can_be_given_its_own_window(view, app):
    """A tab is not a window; this is the arrangement the request was about."""
    view.add_stream(Stream(0x123, name="ECU"))
    view._pop_out_current()
    app.processEvents()

    window = view._windows["123"]
    assert window.parent() is None, "an owner is what costs it the taskbar entry"
    assert window.isVisible() and window.pane.isVisible()
    assert view.tabs.count() == 0, "it left the tabs rather than being copied"
    assert "ECU" in window.windowTitle()


def test_a_popped_out_stream_keeps_reading(view, app):
    view.add_stream(Stream(0x123))
    view._pop_out_current()
    app.processEvents()
    view._on_frames([frame(0x123, b"still here")])
    assert view._texts["123"].toPlainText() == "still here"


def test_closing_the_window_brings_it_back_as_a_tab(view, app):
    view.add_stream(Stream(0x123, name="ECU"))
    view._on_frames([frame(0x123, b"before ")])
    view._pop_out_current()
    app.processEvents()

    view._windows["123"].close()
    app.processEvents()
    assert "123" not in view._windows
    assert view.tabs.count() == 1
    # isHidden, not isVisible: the pane itself is not on screen in a test,
    # so what matters is that reparenting did not leave the widget
    # explicitly hidden, which is how a blank tab happens.
    assert not view._texts["123"].isHidden(), "not a blank tab"
    view._on_frames([frame(0x123, b"after")])
    assert view._texts["123"].toPlainText() == "before after", "and it kept what it had"


def test_a_stream_cannot_be_popped_out_twice(view, app):
    view.add_stream(Stream(0x123))
    view._pop_out_current()
    app.processEvents()
    assert not view.pop_out.isEnabled()


def test_removing_a_stream_takes_its_window_with_it(view, app):
    view.add_stream(Stream(0x123))
    view._pop_out_current()
    app.processEvents()
    view.remove_stream("123")
    app.processEvents()
    assert not view._windows and not view._streams


# --- between runs -------------------------------------------------------------------------
def test_the_ids_are_remembered(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    ctx = Context(log=print)
    first = AsciiView(Channels(), ctx)
    first.add_stream(Stream(0x18FF0102, extended=True, name="log", skip=1))
    first.shutdown()

    second = AsciiView(Channels(), Context(log=print))
    assert [s.to_dict() for s in second.streams()] == [
        {"id": 0x18FF0102, "extended": True, "name": "log", "skip": 1}
    ]
    second.shutdown()
