# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
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
    """Two arrivals are a gap, and a gap is an answer. Waiting adds nothing."""
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
    # Standing in for the CANopen manager, which names these only for
    # nodes it knows are on the bus.
    view.classifiers.append(lambda f: {0x185: "TxPDO1 n5", 0x705: "Heartbeat n5"}.get(f.can_id))
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


def test_a_database_name_beats_a_guess_from_the_id(app, tmp_path, monkeypatch):
    """A database somebody loaded names what it was written to name. The
    predefined connection set claims 0x180 to 0x67F, so reading ids that way
    first left those names with nothing to say."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    view = TraceView(Hooks(ctx), ctx)
    dbc = DbcDecoder()
    dbc.load(resources.path("demo.dbc"))  # names 0x185 DriveStatus, 0x705 DriveHeartbeat
    view.classifiers.append(dbc.message_name)
    view.classifiers.append(lambda f: "TxPDO1 n5" if f.can_id == 0x185 else None)

    view.on_frames(
        [
            frame(0x185, b"\x00" * 8, 0.0),  # named by the database and by the id
            frame(0x123, b"\x00" * 4, 0.2),  # named by the database alone
        ]
    )
    kinds = [view.model.index(r, 4).data() for r in range(2)]
    assert kinds == ["DriveStatus", "PumpCommand"]


def make_view(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    return TraceView(Hooks(ctx), ctx), ctx


def test_filter_box_matches_id_name_channel_and_data(app, tmp_path, monkeypatch):
    view, _ctx = make_view(tmp_path, monkeypatch)
    # Something has to have named a frame for the name to be searchable,
    # and naming is a protocol's job now rather than a guess from the id.
    view.classifiers.append(lambda f: "Heartbeat n5" if f.can_id == 0x705 else None)
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
        "Restore supplied files...",
        "---",
        "Reset everything...",
    ]
    tools = [a.text() for a in _menu(window, "&Tools").actions()]
    assert "Reset" in tools
    assert "Forget remembered folders" not in tools, "moved, not copied"
    window.close()


def test_reset_layout_is_in_both_menus_and_works_from_either(app, tmp_path, monkeypatch):
    """The same action in two places. The View menu is rebuilt whenever a
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


def test_restoring_supplied_files_from_the_menu_keeps_copies(app, tmp_path, monkeypatch):
    from pycangui.core import workspaces
    from pycangui.ui import main_window as mw

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    hooks_file = workspaces.hooks_dir() / "canopen.py"
    hooks_file.write_text("def node_name(identity, *, ctx):\n    return 'mine'\n")
    node_file = workspaces.nodes_dir() / "uds_server.py"
    node_file.write_text("# my ECU\n")

    chosen = [("hooks", "canopen.py"), ("nodes", "uds_server.py")]
    monkeypatch.setattr(mw.RestoreSupplied, "exec", lambda _self: mw.QDialog.Accepted)
    monkeypatch.setattr(mw.RestoreSupplied, "chosen", lambda _self: chosen)
    window._restore_supplied()

    assert "def eds_for_node" in hooks_file.read_text(), "the shipped hook is back"
    assert "return 'mine'" in (hooks_file.parent / "canopen.py.bak").read_text()
    assert "def _routine" in node_file.read_text(), "and the shipped node"
    assert (node_file.parent / "uds_server.py.bak").read_text() == "# my ECU\n"
    log = window.log.toPlainText()
    assert "canopen.py.bak" in log and "uds_server.py.bak" in log
    window.close()


def test_the_restore_dialog_greys_out_what_nobody_has_edited(app, tmp_path, monkeypatch):
    from pycangui.core import workspaces
    from pycangui.ui.restore_supplied import RestoreSupplied

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    edited = workspaces.hooks_dir() / "canopen.py"
    edited.write_text("def node_name(identity, *, ctx):\n    x = 1\n")
    window.hooks.reload()

    groups = {"hooks": window.hooks.supplied, "nodes": window.nodes.supplied}
    dialog = RestoreSupplied(window, groups)
    assert dialog._boxes[("hooks", "canopen.py")].isEnabled()
    assert not dialog._boxes[("hooks", "uds.py")].isEnabled(), "nothing to restore or lose"
    assert not dialog._boxes[("nodes", "uds_server.py")].isEnabled()
    assert dialog.chosen() == [], "nothing is ticked to start with"
    dialog._boxes[("hooks", "canopen.py")].setChecked(True)
    assert dialog.chosen() == [("hooks", "canopen.py")]
    window.close()


# --- timing statistics in the latest-per-id view ----------------------------------
def stats(m, row=0):
    """The five statistics columns, by name."""
    from pycangui.ui.latest_model import COLUMNS

    return {name: m.index(row, COLUMNS.index(name)).data() for name in COLUMNS[10:]}


def test_the_statistics_describe_the_whole_run_not_the_window(app):
    """Rate and Period are deliberately recent, so a stall an hour ago has
    gone from them. These are the columns that still remember it."""
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
    """A replay looping, or a reconnect. The old gaps describe a clock
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
    the constructor, so everything it touches has to exist by then. It did
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


# --- how fast a figure is allowed to change -------------------------------------------
def named(columns):
    """Column numbers as their headings, so a test says what it means."""
    from pycangui.ui.latest_model import COLUMNS

    return {COLUMNS[c] for c in columns}


def measured_columns():
    """The headings of everything worked out from arrivals rather than in them."""
    from pycangui.ui.latest_model import COLUMNS, MEASURED_SPANS

    return {COLUMNS[c] for first, last in MEASURED_SPANS for c in range(first, last + 1)}


def changes_from(model, call):
    """Which columns the model says to repaint, as a set."""
    touched: set[int] = set()
    model.dataChanged.connect(
        lambda lo, hi, _roles=None: touched.update(range(lo.column(), hi.column() + 1))
    )
    call()
    return touched


def test_an_arriving_frame_does_not_repaint_the_measured_columns(app):
    """A figure redrawn on every arrival changes faster than anybody can
    read it. Every arrival repainted the whole row, so the 500 ms timer set
    the pace of nothing."""
    m = LatestModel()
    cyclic(m, 0x100, 0.01, 10)  # measured once, on the second frame

    touched = changes_from(m, lambda: cyclic(m, 0x100, 0.01, 10, start=1.0))

    assert named(touched) >= {"Data", "Count", "Last"}, "what arrived is redrawn as it arrives"
    assert not named(touched) & measured_columns(), "and nothing that was measured is"


def test_the_timer_is_what_repaints_them(app):
    m = LatestModel()
    cyclic(m, 0x100, 0.01, 10)

    touched = changes_from(m, m.refresh_rates)

    assert named(touched) == measured_columns()


def test_a_row_with_no_figure_yet_still_answers_at_once(app):
    """A row that has just appeared, or one that had stopped and started
    again, should say so rather than sitting blank for half a second."""
    m = LatestModel()
    cyclic(m, 0x100, 0.1, 2)  # never refreshed
    assert m.index(0, 7).data() == "10.0 Hz"

    m._rows[0].rate_hz = m._rows[0].period_s = 0.0  # as a stall leaves it
    cyclic(m, 0x100, 0.1, 2, start=10.0)
    assert m.index(0, 7).data(), "it started again, and says so now"


# --- work that blocks, off the window's thread ----------------------------------------
def test_a_manager_runs_a_job_on_its_own_worker(app, tmp_path, monkeypatch):
    """One worker per protocol is what keeps requests sequential: a second
    thread talking to one network is two conversations sharing a listener."""
    from pycangui.canopen.manager import CanopenManager
    from pycangui.core.bus import BusManager

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = CanopenManager(BusManager())
    submitted: list = []
    monkeypatch.setattr(manager._worker, "submit", lambda job, done: submitted.append((job, done)))

    manager.background(lambda: 42, print)

    assert submitted and submitted[0][1] is print, "the caller's own done, handed straight on"
    assert submitted[0][0]() == 42
    manager.shutdown()


def test_without_a_done_the_answer_goes_to_the_log(app, tmp_path, monkeypatch):
    from pycangui.canopen.manager import CanopenManager
    from pycangui.core.bus import BusManager

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    manager = CanopenManager(BusManager())
    said: list = []
    manager.message.connect(lambda text, level: said.append((level, text)))

    manager._said("all done", None)
    manager._said(None, "it went wrong")

    assert said == [("information", "all done"), ("warning", "it went wrong")]
    manager.shutdown()


def test_a_plugin_and_the_console_use_the_same_queue(app, tmp_path, monkeypatch):
    """A plugin reaching into canopen._worker and a console call to
    canopen.background must not be two different answers."""
    import inspect

    from pycangui.ui import plugin_app

    source = inspect.getsource(plugin_app)
    assert "self.canopen.background(job, done)" in source
    assert "_worker.submit" not in source, "no reaching past the public way in"
