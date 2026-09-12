"""Latest-per-id model and transmit pane, headless."""

import time

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QWidget

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core import workspaces
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.hooks import Hooks
from pycangui.ui.latest_model import LatestModel
from pycangui.ui.main_window import DEFAULT_VISIBLE, MainWindow
from pycangui.ui.trace_view import TraceView
from pycangui.ui.tx_view import COL_CYCLIC, COL_DATA, TxView


def frame(can_id: int, data: bytes, t: float, rx: bool = True) -> Frame:
    return Frame(t, "vcan", can_id, False, False, rx, data)


def test_latest_model_one_row_per_id_with_count_and_period(app):
    m = LatestModel()
    m.append([frame(0x100, b"\x01", 0.0), frame(0x200, b"\x02", 0.01), frame(0x100, b"\x03", 0.1)])
    assert m.rowCount() == 2
    row0 = [m.index(0, c).data() for c in range(10)]
    assert row0[0] == "100" and row0[5] == "03" and row0[6] == "2" and row0[8] == "100.0 ms"
    assert m.index(0, 5).data(Qt.ForegroundRole) is not None  # data changed -> highlighted
    assert m.index(1, 5).data(Qt.ForegroundRole) is None
    assert m.index(1, 7).data() == "", "one frame is not a rate"


# --- rate and cycle time -----------------------------------------------------------------
def cyclic(m, can_id, period, count, start=0.0):
    """Feed one id at a fixed period, as a bus would."""
    m.append([frame(can_id, b"", start + period * i) for i in range(count)])


def test_a_rate_appears_as_soon_as_there_are_two_frames(app):
    """Two arrivals are a gap, and a gap is an answer.  Waiting adds nothing."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 2)
    assert m.index(0, 7).data() == "10.0 Hz"


def test_a_fast_message_is_measured_over_recent_arrivals(app):
    m = LatestModel()
    cyclic(m, 0x100, 0.01, 200)  # 100 Hz
    m.refresh_rates()
    assert m._rows[0].rate_hz == pytest.approx(100.0, rel=0.05)


@pytest.mark.parametrize("hz", [2.0, 1.0, 0.5, 0.2])
def test_a_slow_message_is_measured_correctly(app, hz):
    """The old figure counted arrivals per refresh, and half a second holds
    none of these -- so it alternated between nothing and twice the truth."""
    m = LatestModel()
    cyclic(m, 0x100, 1 / hz, 12)
    m.refresh_rates()
    assert m._rows[0].rate_hz == pytest.approx(hz, rel=0.01)
    assert m._rows[0].period_s == pytest.approx(1 / hz, rel=0.01)


def test_a_message_slower_than_the_window_still_gets_an_answer(app):
    """One frame a minute: the only thing available about it is the minute."""
    m = LatestModel()
    cyclic(m, 0x100, 60.0, 3)
    m.refresh_rates()
    assert m._rows[0].period_s == pytest.approx(60.0)
    assert m.index(0, 8).data() == "60.00 s", "read in seconds once it is not milliseconds"


def test_the_rate_and_the_cycle_time_cannot_disagree(app):
    """They are the same measurement, so they are the same measurement."""
    m = LatestModel()
    cyclic(m, 0x100, 0.02, 40)
    m.refresh_rates()
    row = m._rows[0]
    assert row.rate_hz == pytest.approx(1 / row.period_s)


def test_a_stopped_message_has_no_rate(app):
    """Saying it still runs at 50 Hz because it used to is the wrong answer."""
    m = LatestModel()
    cyclic(m, 0x100, 0.02, 40)
    m.refresh_rates()
    assert m._rows[0].rate_hz > 0
    m._rows[0].last_seen -= 5.0  # nothing for five seconds
    m.refresh_rates()
    assert m._rows[0].rate_hz == 0.0
    assert m.index(0, 7).data() == ""


def test_a_slow_message_is_not_mistaken_for_a_stopped_one(app):
    """Two seconds of silence is a stop for a fast message and a gap for this one."""
    m = LatestModel()
    cyclic(m, 0x100, 2.0, 4)  # one every two seconds
    m._rows[0].last_seen -= 3.0
    m.refresh_rates()
    assert m._rows[0].rate_hz == pytest.approx(0.5)


def test_a_changed_rate_is_picked_up_rather_than_averaged_in(app):
    """Arrivals older than the window drop out, so the figure describes now."""
    m = LatestModel()
    cyclic(m, 0x100, 0.5, 10)  # 2 Hz for five seconds
    cyclic(m, 0x100, 0.02, 60, start=10.0)  # then 50 Hz
    m.refresh_rates()
    assert m._rows[0].rate_hz == pytest.approx(50.0, rel=0.1)


def test_time_starting_over_does_not_report_a_negative_rate(app):
    """A reconnect restarts bus time at zero, and a replay loops."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 10)
    cyclic(m, 0x100, 0.1, 10)  # the same timestamps again
    m.refresh_rates()
    assert m._rows[0].rate_hz == pytest.approx(10.0, rel=0.1)


def test_a_rate_below_one_hertz_keeps_a_second_decimal(app):
    """One would round 0.2 Hz to 0.2 and 0.04 Hz to nothing at all."""
    m = LatestModel()
    cyclic(m, 0x100, 5.0, 3)
    m.refresh_rates()
    assert m.index(0, 7).data() == "0.20 Hz"


def wait(app, pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert pred(), "timed out"


def test_tx_view_send_and_cyclic(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    log: list[str] = []
    ctx = Context(log=log.append)
    bus = BusManager()
    received: list[Frame] = []
    bus.frames.connect(received.extend)
    bus.connect_bus("virtual", "vcan_tx", 500000, False)

    canopen = CanopenManager(bus)
    view = TxView(bus, ctx, DbcDecoder(), canopen)
    r = view.add_message({"kind": "raw", "id": "1A3", "data": "de ad be ef", "period": 20})
    view.send_row(r)
    wait(app, lambda: any(f.can_id == 0x1A3 for f in received))
    assert received[-1].data == bytes.fromhex("deadbeef") and received[-1].rx is False

    view.item(r).setCheckState(COL_CYCLIC, Qt.Checked)  # start cyclic
    wait(app, lambda: sum(f.can_id == 0x1A3 for f in received) >= 4)
    view.item(r).setText(COL_DATA, "01 02")  # live edit restarts the task
    wait(app, lambda: any(f.data == b"\x01\x02" for f in received))
    assert r in view._tasks

    bad = view.add_message({"kind": "raw", "id": "zz", "data": "00", "period": 10})
    view.send_row(bad)
    assert any("TX row 2" in line for line in log)

    bus.disconnect_bus()  # stops periodic tasks, unticks Cyclic
    assert view._tasks == {}
    assert view.item(r).checkState(COL_CYCLIC) == Qt.Unchecked

    saved = ctx.settings.get("tx.messages")
    assert saved[0]["id"] == "1A3" and saved[0]["data"] == "01 02"
    canopen.shutdown()  # a QThread still running when Qt destroys it aborts


def test_trace_view_kind_column_and_filter(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    view.on_frames([frame(0x185, b"", 0.0), frame(0x705, b"", 0.1), frame(0x123, b"", 0.2)])
    kinds = [view.model.index(r, 4).data() for r in range(3)]
    assert kinds == ["TxPDO1 n5", "Heartbeat n5", ""]
    assert view.table.model().rowCount() == 3
    view._group_actions["PDO"].setChecked(False)
    view._group_actions["Other"].setChecked(False)
    assert view.table.model().rowCount() == 1  # only the heartbeat survives
    assert view.latest_table.model().rowCount() == 1
    assert ctx.settings.get("trace.hidden_groups") == ["Other", "PDO"]
    view._show_all()
    assert view.table.model().rowCount() == 3

    # a user hook can relabel frames; the first word picks the group
    hook_src = "def frame_kind(frame, *, ctx):" + chr(10)
    hook_src += "    return 'Pump status' if frame.can_id == 0x123 else None" + chr(10)
    (workspaces.hooks_dir() / "trace.py").write_text(hook_src)
    view.hooks.reload()
    view.on_frames([frame(0x123, b"", 0.3)])
    assert view.model.index(3, 4).data() == "Pump status"


def test_trace_columns_show_the_channel_and_fd(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    standard = Frame(0.0, "CAN 1", 0x185, False, False, True, b"\x01\x02")
    extended = Frame(0.1, "Drive bus", 0x18DAF110, True, True, True, bytes(12))
    view.on_frames([standard, extended])
    row0 = [view.model.index(0, c).data() for c in range(7)]
    row1 = [view.model.index(1, c).data() for c in range(7)]
    assert row0[1] == "CAN 1" and row1[1] == "Drive bus"  # the channel, not "CAN"/"CANx"
    assert row0[3] == "185" and row1[3] == "18DAF110"  # 11 vs 29-bit is clear from the id
    assert row0[5] == "2" and row1[5] == "12 FD"  # FD is marked on the length
    # the same in the latest-per-id view
    assert view.latest.index(0, 2).data() == "CAN 1"
    assert view.latest.index(1, 2).data() == "Drive bus"
    assert view.latest.index(1, 4).data() == "12 FD"


def test_canopen_label_beats_a_dbc_message_name(app, tmp_path, monkeypatch):
    """A DBC that names 0x185 must not hide which node sent it."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    dbc = DbcDecoder()
    dbc.load(resources.path("demo.dbc"))  # names 0x185 DriveStatus, 0x705 DriveHeartbeat
    view.classifiers.append(dbc.message_name)

    view.on_frames(
        [
            frame(0x185, b"\x00" * 8, 0.0),  # a CANopen TPDO the DBC also names
            frame(0x705, b"\x05", 0.1),  # a heartbeat the DBC also names
            frame(0x123, b"\x00" * 4, 0.2),  # not CANopen: the DBC name stands
        ]
    )
    kinds = [view.model.index(r, 4).data() for r in range(3)]
    assert kinds == ["TxPDO1 n5", "Heartbeat n5", "PumpCommand"]


def make_view(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    return TraceView(Hooks(ctx), ctx), ctx


def test_filter_box_matches_id_name_channel_and_data(app, tmp_path, monkeypatch):
    view, _ctx = make_view(tmp_path, monkeypatch)
    view.on_frames(
        [
            Frame(0.0, "CAN 1", 0x185, False, False, True, b"\xde\xad"),
            Frame(0.1, "CAN 1", 0x705, False, False, True, b"\x05"),
            Frame(0.2, "Drive bus", 0x185, False, False, True, b"\x00"),
        ]
    )
    shown = lambda: view.table.model().rowCount()  # noqa: E731
    assert shown() == 3

    view.search.setText("185")  # by id
    assert shown() == 2
    view.search.setText("heartbeat")  # by decoded name
    assert shown() == 1
    view.search.setText("drive bus")  # by channel, spaces and all
    assert shown() == 1
    view.search.setText("de ad")  # by data
    assert shown() == 1
    view.search.setText("185 drive")  # every word must match
    assert shown() == 1
    view.search.setText("185 nonsense")
    assert shown() == 0
    view.search.clear()
    assert shown() == 3
    # the latest-per-id view filters with the same terms
    view.search.setText("705")
    assert view.latest_table.model().rowCount() == 1


def test_channels_can_be_hidden(app, tmp_path, monkeypatch):
    view, ctx = make_view(tmp_path, monkeypatch)
    view.on_frames(
        [
            Frame(0.0, "CAN 1", 0x185, False, False, True, b""),
            Frame(0.1, "Drive bus", 0x186, False, False, True, b""),
        ]
    )
    # channels appear in the filter menu as soon as traffic is seen on them
    assert set(view._channel_actions) == {"CAN 1", "Drive bus"}
    view._channel_actions["Drive bus"].setChecked(False)
    assert view.table.model().rowCount() == 1
    assert ctx.settings.get("trace.hidden_channels") == ["Drive bus"]
    view._show_all()
    assert view.table.model().rowCount() == 2


def test_pause_holds_the_display_without_losing_frames(app, tmp_path, monkeypatch):
    view, _ctx = make_view(tmp_path, monkeypatch)
    view.on_frames([Frame(0.0, "CAN 1", 0x100, False, False, True, b"")])
    assert view.model.rowCount() == 1

    view.pause.setChecked(True)
    view.on_frames([Frame(0.1, "CAN 1", 0x101, False, False, True, b"") for _ in range(5)])
    assert view.model.rowCount() == 1  # the display has not moved
    assert len(view._pending) == 5  # but nothing was thrown away
    assert "held" in view.count_label.text()

    view.pause.setChecked(False)
    assert view.model.rowCount() == 6 and view._pending == []


def test_copy_selection_puts_rows_on_the_clipboard(app, tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication

    view, _ctx = make_view(tmp_path, monkeypatch)
    view.on_frames(
        [
            Frame(0.0, "CAN 1", 0x185, False, False, True, b"\x01\x02"),
            Frame(0.1, "CAN 1", 0x705, False, False, True, b"\x05"),
        ]
    )
    view.table.selectRow(0)
    view.copy_selection()
    text = QGuiApplication.clipboard().text()
    lines = text.splitlines()
    assert lines[0].split("\t")[:5] == ["Time", "Channel", "Dir", "ID", "Kind"]
    assert lines[1].split("\t")[1] == "CAN 1"
    assert lines[1].split("\t")[3] == "185"
    assert len(lines) == 2  # header plus the one selected row


def test_no_pane_hides_a_qwidget_method(app, tmp_path, monkeypatch):
    """A line edit stored as self.size hides QWidget.size().

    DetachedPane asks the pane it is handed for its size, so a pane with an
    attribute named after a QWidget method cannot be detached -- and nothing
    else would have said so until somebody tried it.
    """
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    hidden = [
        f"{name}.{attr}"
        for name in window.panes.names()
        # The pane, not the dock's widget: that is a container holding the
        # button strip and a scroll area, and asking it walked six Qt signals
        # and none of the panes this was written to check.
        if (pane := window.panes.view(name)) is not None
        for attr, value in vars(pane).items()
        # Qt keeps its own bound signals in here too, and those are meant to be
        # there; a child widget under one of those names is the mistake.
        if isinstance(value, QWidget) and callable(getattr(QWidget, attr, None))
    ]
    assert not hidden, f"these shadow a QWidget method: {hidden}"
    window.close()


def test_the_transmit_buttons_are_grouped_by_what_they_do(app, tmp_path, monkeypatch):
    """Send and Stop all touch the bus; the rest only edit the list."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    bar = window.tx.layout().itemAt(0).layout()
    buttons = [
        bar.itemAt(i).widget().text()
        for i in range(bar.count())
        if bar.itemAt(i).widget() is not None
    ]
    assert buttons == [
        "Send selected",
        "Stop all cyclic",
        "Add",
        "Counter / checksum...",
        "Remove selected",
    ]
    sources = [a.text() for a in window.tx.add_menu.actions()]
    assert sources == ["Raw message", "From DBC...", "CANopen RPDO..."], "the three, in a menu"
    window.close()


@pytest.fixture
def tx(app, tmp_path, monkeypatch):
    """A transmit pane with three rows, on a virtual bus so cyclic can start."""
    from pycangui.ui.tx_view import DEFAULT_RAW, TxView

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    bus.connect_bus("virtual", "vcan_tx_view", 500000, False)
    view = TxView(bus, Context(log=print), DbcDecoder(), CanopenManager(bus))
    for _ in range(3):
        view.add_message(dict(DEFAULT_RAW))
    yield view
    view.stop_all()
    bus.disconnect_bus()


def space_over(view):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent

    return view.eventFilter(view.tree, QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier, " "))


def cyclic_states(view):
    from pycangui.ui.tx_view import COL_CYCLIC

    return [view.item(r).checkState(COL_CYCLIC) == Qt.Checked for r in range(view.message_count())]


def test_several_rows_can_be_selected_at_once(tx):
    """Send selected and Remove selected always looped over the selection.

    The tree was left on single selection, so they never got more than one.
    """
    tx.tree.selectAll()
    assert len(tx.tree.selectedItems()) == 3


def test_space_ticks_cyclic_on_everything_selected(tx):
    assert cyclic_states(tx) == [False, False, False]
    tx.tree.selectAll()

    space_over(tx)
    assert cyclic_states(tx) == [True] * 3, "one unticked among them means tick them all"
    space_over(tx)
    assert cyclic_states(tx) == [False] * 3, "and pressing it again stops them"


def test_space_with_nothing_selected_is_left_to_qt(tx):
    tx.tree.clearSelection()
    assert not space_over(tx), "not swallowed, so Qt still gets it"


def test_the_tooltip_says_how_to_tick_them_all(tx):
    assert "press space" in tx.tree.toolTip()
    assert "Ctrl+A" in tx.tree.toolTip(), "and how to select them in the first place"


def _menu(window, title):
    return next(a.menu() for a in window.menuBar().actions() if a.text() == title)


def test_backends_are_their_own_section_below_the_hook_entries(app, tmp_path, monkeypatch):
    """Backends are the other workspace folder, not a fourth hook entry."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    entries = ["---" if a.isSeparator() else a.text() for a in _menu(window, "&Tools").actions()]
    assert entries[:6] == [
        "Open hooks folder",
        "Reload hooks",
        "Add missing hooks",
        "---",
        "Open backends folder",
        "---",
    ]
    window.close()


def test_the_two_hook_entries_say_how_they_differ(app, tmp_path, monkeypatch):
    """Reload and Add missing hooks are a confusing pair by name alone."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    tools = _menu(window, "&Tools")
    assert tools.toolTipsVisible(), "tooltips in this menu are set but never shown"
    tips = {a.text(): a.toolTip() for a in tools.actions()}
    assert "without" in tips["Reload hooks"] and "restarting" in tips["Reload hooks"]
    assert "by themselves" in tips["Add missing hooks"], "says it is not the usual route"
    assert "alone" in tips["Add missing hooks"], "says what it will not touch"
    window.close()


def test_the_resets_are_together_in_one_submenu(app, tmp_path, monkeypatch):
    """Putting something back the way it was is one thing to go looking
    for, not three entries scattered down a menu."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    entries = ["---" if a.isSeparator() else a.text() for a in window.reset_menu.actions()]
    assert entries == [
        "Reset layout",
        "Forget remembered folders",
        "Ask about everything again",
        "Restore hook files...",
        "---",
        "Reset everything...",
    ]
    tools = [a.text() for a in _menu(window, "&Tools").actions()]
    assert "Reset" in tools
    assert "Forget remembered folders" not in tools, "moved, not copied"
    window.close()


def test_reset_layout_is_in_both_menus_and_works_from_either(app, tmp_path, monkeypatch):
    """The same action in two places.  The View menu is rebuilt whenever a
    pane comes or goes, and clear() deletes the actions a menu owns -- so an
    action belonging to that menu would be destroyed out from under the
    Tools entry still pointing at it."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    assert window.reset_layout_action in window.view_menu.actions()
    assert window.reset_layout_action in window.reset_menu.actions()

    window.panes.add("trace")  # rebuilds the View menu
    app.processEvents()
    window._build_view_menu()
    assert window.reset_layout_action in window.reset_menu.actions(), "the Tools copy went"

    window.reset_layout_action.trigger()  # and it still does something
    app.processEvents()
    # isHidden rather than isVisible: this window was never shown, and an
    # unshown window's children are all invisible whatever the layout says.
    showing = {name for name, dock in window.panes.docks.items() if not dock.isHidden()}
    assert showing == set(DEFAULT_VISIBLE)
    window.close()


def test_reset_layout_puts_the_window_back_to_the_size_it_opens_at(app, tmp_path, monkeypatch):
    """A layout reset inside a window somebody shrank to a third of the
    screen is not the arrangement it promises: the proportions assume
    something like the size it opens at."""
    from PySide6.QtWidgets import QApplication

    from pycangui.ui.main_window import DEFAULT_HEIGHT, DEFAULT_WIDTH

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    window.show()
    app.processEvents()
    window.resize(500, 400)
    app.processEvents()

    window._reset_layout()
    app.processEvents()

    available = (window.screen() or QApplication.primaryScreen()).availableGeometry()
    assert window.width() == min(DEFAULT_WIDTH, available.width())
    assert window.height() == min(DEFAULT_HEIGHT, available.height())
    window.close()


def test_restoring_a_hook_file_from_the_menu_keeps_a_copy(app, tmp_path, monkeypatch):
    from pycangui.core import workspaces
    from pycangui.ui import main_window as mw

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    hooks_file = workspaces.hooks_dir() / "canopen.py"
    hooks_file.write_text("def node_name(identity, *, ctx):\n    return 'mine'\n")

    monkeypatch.setattr(mw.RestoreHooks, "exec", lambda _self: mw.QDialog.Accepted)
    monkeypatch.setattr(mw.RestoreHooks, "chosen", lambda _self: ["canopen"])
    window._restore_hooks()

    assert "def eds_for_node" in hooks_file.read_text(), "the shipped one is back"
    assert "return 'mine'" in (hooks_file.parent / "canopen.py.bak").read_text()
    assert any("canopen.py.bak" in line for line in window.log.toPlainText().splitlines())
    window.close()


def test_the_restore_dialog_greys_out_what_nobody_has_edited(app, tmp_path, monkeypatch):
    from pycangui.core import workspaces
    from pycangui.ui.restore_hooks import RestoreHooks

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    edited = workspaces.hooks_dir() / "canopen.py"
    edited.write_text("def node_name(identity, *, ctx):\n    x = 1\n")
    window.hooks.reload()

    dialog = RestoreHooks(window, window.hooks)
    assert dialog._boxes["canopen"].isEnabled()
    assert not dialog._boxes["uds"].isEnabled(), "nothing to restore and nothing to lose"
    assert dialog.chosen() == [], "nothing is ticked to start with"
    dialog._boxes["canopen"].setChecked(True)
    assert dialog.chosen() == ["canopen"]
    window.close()


# --- timing statistics in the latest-per-id view ----------------------------------
def stats(m, row=0):
    """The five statistics columns, by name."""
    from pycangui.ui.latest_model import COLUMNS

    return {name: m.index(row, COLUMNS.index(name)).data() for name in COLUMNS[10:]}


def test_the_statistics_describe_the_whole_run_not_the_window(app):
    """Rate and Period are deliberately recent, so a stall an hour ago has
    gone from them.  These are the columns that still remember it."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 20)  # 100 ms apart
    m.append([frame(0x100, b"", 1.9 + 0.5)])  # then one that was late
    m.append([frame(0x100, b"", 2.5)])  # and back to normal
    m.refresh_rates()

    got = stats(m)
    assert got["First"] == "0.000"
    assert got["Period min"] == "100.0 ms"
    assert got["Period max"] == "500.0 ms"
    assert got["Jitter"] == "400.0 ms"
    assert m._rows[0].gap_avg == pytest.approx((2.5 - 0.0) / 21)


def test_one_frame_has_no_timing_to_report(app):
    m = LatestModel()
    m.append([frame(0x100, b"", 1.25)])
    got = stats(m)
    assert got["First"] == "1.250"
    assert [got[n] for n in ("Period min", "Period avg", "Period max", "Jitter")] == [""] * 4


def test_a_perfectly_regular_message_says_zero_rather_than_nothing(app):
    """Blank means "not known yet" in the other columns, so jitter cannot
    use it for "none" -- that is the answer somebody is looking for."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 5)
    assert stats(m)["Jitter"] == "0.0 ms"


def test_time_starting_over_starts_the_statistics_over(app):
    """A replay looping, or a reconnect.  The old gaps describe a clock
    that no longer runs, and one of them would be minus twenty seconds."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 10)
    cyclic(m, 0x100, 0.2, 5)  # the same timestamps again, slower
    got = stats(m)
    assert got["First"] == "0.000"
    assert got["Period min"] == "200.0 ms", "nothing from before the restart"
    assert got["Period max"] == "200.0 ms"


def test_the_statistics_columns_sort_by_number(app):
    """Sorted as text, 90.0 ms would come after 100.0 ms."""
    from pycangui.ui.latest_model import COLUMNS

    m = LatestModel()
    cyclic(m, 0x100, 0.1, 5)
    assert m.index(0, COLUMNS.index("Jitter")).data(Qt.UserRole) == pytest.approx(0.0)
    assert m.index(0, COLUMNS.index("Period max")).data(Qt.UserRole) == pytest.approx(0.1)


def test_the_headers_say_which_columns_mean_now_and_which_mean_since(app):
    m = LatestModel()
    from pycangui.ui.latest_model import COLUMNS

    rate = m.headerData(COLUMNS.index("Rate"), Qt.Horizontal, Qt.ToolTipRole)
    avg = m.headerData(COLUMNS.index("Period avg"), Qt.Horizontal, Qt.ToolTipRole)
    assert "describes now" in rate
    assert "not the same as Period" in avg


def test_the_statistics_start_hidden_and_the_choice_is_kept(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    from pycangui.ui.latest_model import COLUMNS

    assert view.latest_table.isColumnHidden(COLUMNS.index("Jitter"))
    assert not view.latest_table.isColumnHidden(COLUMNS.index("Rate"))

    jitter = next(a for a in view._column_menus[1].actions() if a.text() == "Jitter")
    jitter.setChecked(True)
    assert not view.latest_table.isColumnHidden(COLUMNS.index("Jitter"))
    assert "Jitter" not in ctx.settings.get("trace.latest_columns")

    again = TraceView(Hooks(ctx), ctx)
    assert not again.latest_table.isColumnHidden(COLUMNS.index("Jitter")), "not remembered"


def test_each_view_has_its_own_columns(app, tmp_path, monkeypatch):
    """The two tables answer different questions and share no column but
    the word column."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    assert [a.text() for a in view._column_menus[0].actions()][:3] == ["Time", "Channel", "Dir"]
    assert [a.text() for a in view._column_menus[1].actions()][:3] == ["ID", "Kind", "Channel"]
    assert view.columns_button.menu() is view._column_menus[view.mode.currentIndex()]


def test_a_trace_left_in_latest_mode_opens_again(app, tmp_path, monkeypatch):
    """Restoring the saved mode fires the mode-changed handler from inside
    the constructor, so everything it touches has to exist by then.  It did
    not, and opening a workspace left in Latest per ID threw."""
    import sys

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    first = TraceView(Hooks(ctx), ctx)
    first.mode.setCurrentIndex(1)
    assert ctx.settings.get("trace.mode") == "Latest per ID"

    # Watched through the excepthook, because PySide sends an exception
    # raised inside a slot there rather than to whoever emitted the signal:
    # the constructor finishes, the pane is half built, and the only sign is
    # a traceback in the log.
    blew_up: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *what: blew_up.append(what))

    again = TraceView(Hooks(ctx), ctx)
    assert not blew_up, blew_up
    assert again.mode.currentIndex() == 1
    assert again.stack.currentWidget() is again.latest_table
    assert again.columns_button.menu() is again._column_menus[1]
