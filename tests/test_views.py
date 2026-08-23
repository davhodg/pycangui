"""Latest-per-id model and transmit pane, headless."""

import time

from PySide6.QtCore import Qt

from pycangui import resources
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.core.dbc import DbcDecoder
from pycangui.core.hooks import Hooks
from pycangui.ui.latest_model import LatestModel
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
    assert m.index(0, 7).data() == ""  # no rate until refreshed


def test_latest_model_rate(app):
    m = LatestModel()
    m.append([frame(0x100, b"", 0.0)])
    m._rows[0].last_rate_time -= 1.0  # pretend a second has elapsed
    m.append([frame(0x100, b"", 0.1 * i) for i in range(1, 10)])  # 9 more frames
    m.refresh_rates()
    assert m.index(0, 7).data().endswith("Hz")
    assert 8.0 <= m._rows[0].rate_hz <= 10.5


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
    (tmp_path / "hooks" / "trace.py").write_text(hook_src)
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
