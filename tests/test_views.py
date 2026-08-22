"""Latest-per-id model and transmit pane, headless."""

import time

from PySide6.QtCore import Qt

from pycangui.core.bus import BusManager, Frame
from pycangui.core.context import Context
from pycangui.ui.latest_model import LatestModel
from pycangui.ui.tx_view import COL_CYCLIC, COL_DATA, TxView


def frame(can_id: int, data: bytes, t: float, rx: bool = True) -> Frame:
    return Frame(t, "vcan", can_id, False, False, rx, data)


def test_latest_model_one_row_per_id_with_count_and_period(app):
    m = LatestModel()
    m.append([frame(0x100, b"\x01", 0.0), frame(0x200, b"\x02", 0.01), frame(0x100, b"\x03", 0.1)])
    assert m.rowCount() == 2
    row0 = [m.index(0, c).data() for c in range(9)]
    assert row0[0] == "100" and row0[4] == "03" and row0[5] == "2" and row0[7] == "100.0 ms"
    assert m.index(0, 4).data(Qt.ForegroundRole) is not None  # data changed -> highlighted
    assert m.index(1, 4).data(Qt.ForegroundRole) is None
    assert m.index(0, 6).data() == ""  # no rate until refreshed


def test_latest_model_rate(app):
    m = LatestModel()
    m.append([frame(0x100, b"", 0.0)])
    m._rows[0].last_rate_time -= 1.0  # pretend a second has elapsed
    m.append([frame(0x100, b"", 0.1 * i) for i in range(1, 10)])  # 9 more frames
    m.refresh_rates()
    assert m.index(0, 6).data().endswith("Hz")
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

    view = TxView(bus, ctx)
    r = view.add_row({"id": "1A3", "data": "de ad be ef", "period": 20})
    view.send_row(r)
    wait(app, lambda: any(f.can_id == 0x1A3 for f in received))
    assert received[-1].data == bytes.fromhex("deadbeef") and received[-1].rx is False

    view.table.item(r, COL_CYCLIC).setCheckState(Qt.Checked)  # start cyclic
    wait(app, lambda: sum(f.can_id == 0x1A3 for f in received) >= 4)
    view.table.item(r, COL_DATA).setText("01 02")  # live edit restarts the task
    wait(app, lambda: any(f.data == b"\x01\x02" for f in received))
    assert r in view._tasks

    bad = view.add_row({"id": "zz", "data": "00", "period": 10})
    view.send_row(bad)
    assert any("TX row 2" in line for line in log)

    bus.disconnect_bus()  # stops periodic tasks, unticks Cyclic
    assert view._tasks == {}
    assert view.table.item(r, COL_CYCLIC).checkState() == Qt.Unchecked

    saved = ctx.settings.get("tx.messages")
    assert saved[0]["id"] == "1A3" and saved[0]["data"] == "01 02"
