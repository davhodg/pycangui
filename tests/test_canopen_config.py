# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""PDO configuration, DCF save/apply, store/restore and SYNC, against the demo node."""

import time

import pytest
from PySide6.QtCore import QCoreApplication, Qt

from pycangui import resources
from pycangui.canopen import PdoEntry, load_od
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager


class _Empty:
    """Stands in for a config that has not arrived yet, so a wait can ask
    about its entries without checking for None first."""

    entries = ()


def wait_until(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app, tmp_path, monkeypatch, demo_device):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_cfg", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: manager.node(5) is not None and len(manager.node(5).object_dictionary))
    yield manager, demo, tmp_path
    manager.shutdown()
    bus.disconnect_bus()


def named(manager, node_id=5):
    return {f"{c.direction}{c.number}": c for c in manager.pdo_configs(node_id)}


def test_pdo_configs_from_the_eds(stack):
    manager, _demo, _tmp = stack
    # Wait for the mapping to be *complete*, not merely present. The configs
    # are read over SDO one entry at a time, so a truthy list is a list that
    # may still be filling -- and asserting on it caught TPDO1 with two of its
    # three entries whenever the machine was busy enough.
    wait_until(lambda: len(named(manager).get("TPDO1", _Empty()).entries) == 3)
    configs = named(manager)
    assert set(configs) >= {"TPDO1", "RPDO1"}

    tpdo = configs["TPDO1"]
    assert tpdo.cob_id == 0x185 and tpdo.enabled
    assert [e.name for e in tpdo.entries] == [
        "Statusword",
        "Measurements.Motor speed",
        "Measurements.Odometer",
    ]
    assert tpdo.bits == 64
    assert tpdo.transmission_type == 255
    assert tpdo.transmission_text() == "255 (event, profile)"

    rpdo = configs["RPDO1"]
    assert rpdo.cob_id == 0x205
    assert [(e.index, e.subindex, e.bits) for e in rpdo.entries] == [(0x2001, 0, 16)]


def test_read_and_write_pdo_config(stack):
    manager, demo, _tmp = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))
    manager.read_pdo_config(5)
    wait_until(lambda: any("PDO(s) configured" in m for m in messages))

    config = next(c for c in manager.pdo_configs(5) if c.direction == "RPDO")
    config.event_timer_ms = 0
    config.transmission_type = 254
    config.entries = [PdoEntry(0x2001, 0, 16, "Speed demand")]
    n = len(messages)
    manager.write_pdo_config(config)
    wait_until(lambda: len(messages) > n)
    assert "RPDO1 written to node 5" in messages[-1]
    # the node really has the new transmission type
    assert demo["canopen_device"].state.device.get_data(0x1400, 2)[0] == 254


def test_dcf_save_and_apply(stack):
    manager, demo, tmp_path = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))

    # change something on the node, capture it, change it back, then restore it
    manager.sdo_write(5, 0x2001, 0, "-1234")
    wait_until(
        lambda: (
            demo["canopen_device"].state.device.get_data(0x2001, 0)
            == (-1234).to_bytes(2, "little", signed=True)
        )
    )

    dcf = tmp_path / "node5.dcf"
    manager.save_dcf(5, str(dcf))
    wait_until(lambda: any("DCF written" in m for m in messages), timeout=20)
    assert dcf.is_file()
    text = dcf.read_text(errors="replace")
    assert "[DeviceComissioning]" in text and "ParameterValue" in text

    manager.sdo_write(5, 0x2001, 0, "0")
    wait_until(lambda: demo["canopen_device"].state.device.get_data(0x2001, 0) == b"\x00\x00")

    n = len(messages)
    manager.apply_dcf(5, str(dcf))
    wait_until(
        lambda: any("parameters written from the DCF" in m for m in messages[n:]), timeout=20
    )
    assert demo["canopen_device"].state.device.get_data(0x2001, 0) == (-1234).to_bytes(
        2, "little", signed=True
    )


def test_store_restore_and_sync(stack):
    manager, _demo, _tmp = stack
    messages: list[str] = []
    manager.message.connect(lambda text, _level: messages.append(text))

    manager.store_parameters(5)  # the demo node has no 0x1010: expect a clean error
    wait_until(lambda: messages)
    assert "Node 5:" in messages[-1]

    assert not manager.sync_running
    manager.start_sync(0.05)
    assert manager.sync_running
    wait_until(lambda: any("SYNC started" in m for m in messages))
    manager.stop_sync()
    assert not manager.sync_running
    wait_until(lambda: any("SYNC stopped" in m for m in messages))


# --- where things sit in the pane -------------------------------------------------------
def test_the_object_dictionary_is_the_first_tab(app, tmp_path, monkeypatch):
    """The dictionary, the live PDOs and LSS all want the height, and
    sharing it left every one of them too short to read."""
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    tabs = window.canopen_view.bottom_tabs

    assert tabs.tabText(0) == "Object dictionary"
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Object dictionary",
        "Live PDOs",
        "PDO configuration",
        "Emergencies",
        "Faults",
        "LSS",
        "CANopen log",
    ]
    window.close()


def test_the_sync_rate_lives_in_the_settings(app, tmp_path, monkeypatch):
    """A rate is a fact about the bus, agreed once, not a decision to take
    each time synchronous PDOs are wanted."""
    from PySide6.QtCore import QSettings

    from pycangui.ui import canopen_settings
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.canopen_view

    assert not hasattr(view, "sync_period"), "no box beside the button any more"
    assert view.sync_btn.isCheckable()

    window.ctx.settings.set(canopen_settings.SYNC_KEY, 250)
    started: list[float] = []
    monkeypatch.setattr(window.canopen, "start_sync", started.append)
    view.sync_btn.setChecked(True)

    assert started == [0.25], "the period comes from the settings"
    window.close()


# --- the SYNC counter (0x1019) --------------------------------------------------------------
def sync_frames(channel: str, how_many: int, start, timeout: float = 5.0) -> list[bytes]:
    """The data of the SYNC frames on a virtual channel, heard from beside it."""
    import can

    with can.Bus(interface="virtual", channel=channel) as listener:
        start()
        heard: list[bytes] = []
        deadline = time.monotonic() + timeout
        while len(heard) < how_many and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            message = listener.recv(0.05)
            if message is not None and message.arbitration_id == 0x80:
                heard.append(bytes(message.data))
        return heard


@pytest.fixture
def on_a_bus(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_sync_counter", 500000, False)
    yield manager
    manager.stop_sync()
    bus.disconnect_bus()
    manager.shutdown()


def test_sync_counts_to_the_overflow_and_round_again(on_a_bus):
    heard = sync_frames("vcan_sync_counter", 7, lambda: on_a_bus.start_sync(0.01, 3))
    assert [frame[0] for frame in heard] == [1, 2, 3, 1, 2, 3, 1]
    assert all(len(frame) == 1 for frame in heard)
    on_a_bus.stop_sync()
    assert not on_a_bus.sync_running


def test_sync_with_no_counter_is_the_empty_frame(on_a_bus):
    heard = sync_frames("vcan_sync_counter", 3, lambda: on_a_bus.start_sync(0.01))
    assert heard == [b"", b"", b""]


def test_the_counter_is_a_setting_and_off_until_it_is_set(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui import canopen_settings
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    view = window.canopen_view
    assert canopen_settings.load(window.ctx).sync_counter_overflow == 0

    dialog = canopen_settings.CanopenSettingsDialog(None, canopen_settings.load(window.ctx))
    assert dialog.settings().sync_counter_overflow == 0, "shown as none"
    dialog.sync_counter.setValue(12)
    canopen_settings.save(window.ctx, dialog.settings())

    started = []
    monkeypatch.setattr(window.canopen, "start_sync", lambda *args: started.append(args))
    view.sync_btn.setChecked(True)
    assert started == [(0.1, 12)], "the period and the counter both come from the settings"
    window.close()


@pytest.mark.parametrize(("typed", "kept"), [(0, 0), (1, 0), (2, 2), (240, 240), (999, 240)])
def test_only_what_0x1019_can_hold_is_kept(typed, kept):
    from pycangui.ui import canopen_settings

    assert canopen_settings.sync_overflow(typed) == kept


# --- DOMAIN objects: read with the rest, unless the setting says not ------------------------
def test_a_dcf_saved_from_the_node_has_its_domain_objects(stack):
    manager, _demo, tmp = stack
    done: list[str] = []
    manager.message.connect(lambda text, _level: done.append(text))

    with_them = tmp / "with.dcf"
    assert manager.read_domains, "on unless it is switched off"
    manager.save_dcf(5, str(with_them))
    wait_until(lambda: with_them.is_file() and any("DCF" in m for m in done))
    kept = load_od(with_them).get_variable(0x1021, 0)
    assert kept.value == resources.path("demo.eds").read_bytes(), "the block, byte for byte"

    done.clear()
    without = tmp / "without.dcf"
    manager.read_domains = False
    manager.save_dcf(5, str(without))
    wait_until(lambda: without.is_file() and any("DCF" in m for m in done))
    assert load_od(without).get_variable(0x1021, 0).value is None, "left out"


def test_read_all_includes_domain_objects_when_the_setting_is_on(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui import canopen_settings
    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    try:
        view, manager = window.canopen_view, window.canopen
        assert canopen_settings.load(window.ctx).read_domains and manager.read_domains

        domain = load_od(resources.path("demo.eds")).get_variable(0x1021, 0)
        number = load_od(resources.path("demo.eds")).get_variable(0x2001, 0)
        assert manager.reads(domain) and manager.reads(number)

        dialog = canopen_settings.CanopenSettingsDialog(None, canopen_settings.load(window.ctx))
        dialog.read_domains.setChecked(False)
        chosen = dialog.settings()
        canopen_settings.save(window.ctx, chosen)
        canopen_settings.apply(manager, chosen)
        assert not manager.reads(domain) and manager.reads(number), "only the blocks are left out"
        assert not canopen_settings.load(window.ctx).read_domains, "and it is remembered"
        assert view.manager is manager
    finally:
        window.close()


def test_a_long_block_is_cut_short_in_the_tree():
    from pycangui.canopen.display import Display
    from pycangui.ui.canopen_view import MOST_BYTES_SHOWN, _cell_text, _typed_value

    short, long = bytes(range(8)), bytes(1000)
    assert _cell_text(Display(), short) == short.hex(" ").upper()
    shown = _cell_text(Display(), long)
    assert len(shown) < 4 * MOST_BYTES_SHOWN + 40

    domain = load_od(resources.path("demo.eds")).get_variable(0x1021, 0)
    raw, why = _typed_value(domain, Display(), shown)
    assert raw is None and why, "what is shown cut short cannot be written back as the block"


# --- Read all, as one job that says how far it has got ---------------------------------------
def test_read_all_says_how_far_and_that_it_finished(stack):
    manager, _demo, _tmp = stack
    wanted = [(0x1000, 0), (0x1018, 1), (0x2001, 0)]
    progress, results, finished = [], [], []
    manager.read_progress.connect(lambda n, done, total: progress.append((done, total)))
    manager.sdo_result.connect(lambda n, index, sub, value, error: results.append((index, sub)))
    manager.read_finished.connect(lambda *args: finished.append(args))
    manager.read_many(5, wanted)
    wait_until(lambda: finished)
    assert finished == [(5, 3, 3, False)]
    assert results == wanted, "each answer as a single read's would be"
    assert [done for done, _total in progress] == [0, 1, 2]


DIRECT = Qt.ConnectionType.DirectConnection


def test_read_all_can_be_stopped(stack):
    manager, _demo, _tmp = stack
    finished = []
    manager.read_finished.connect(lambda *args: finished.append(args))
    # Stopped from the reading thread itself, after the first answer: left to
    # the window's thread, a quick machine has read all fifty before it is told.
    manager.sdo_result.connect(lambda *_args: manager.stop_batch(), DIRECT)
    manager.read_many(5, [(0x1000, 0)] * 50)
    wait_until(lambda: finished)
    _node, done, total, stopped = finished[0]
    assert stopped and done < total == 50


def test_a_stopped_dcf_save_writes_no_file(stack, monkeypatch):
    manager, _demo, tmp = stack
    finished = []
    manager.dcf_finished.connect(lambda *args: finished.append(args))
    manager.dcf_progress.connect(lambda *_args: manager.stop_batch(), DIRECT)
    dcf = tmp / "half.dcf"
    manager.save_dcf(5, str(dcf))
    wait_until(lambda: finished)
    assert not dcf.exists(), "half a configuration is not one"
    assert finished[0][2] is False, "stopped is not something going wrong"


def test_a_removed_node_is_forgotten(stack):
    manager, _demo, _tmp = stack
    manager.add_node(9)
    assert 9 in manager.nodes()
    manager.remove_node(9)
    assert 9 not in manager.nodes() and manager.node(9) is None
    manager.remove_node(5)
    assert manager.node(5) is None and manager.eds_path(5) is None


# --- settings: say what changed, and offer to restart what is running ------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from pycangui.ui.main_window import MainWindow

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    window = MainWindow()
    yield window
    window.close()


def answer_settings(monkeypatch, change=lambda dialog: None):
    from PySide6.QtWidgets import QDialog

    from pycangui.ui.canopen_settings import CanopenSettingsDialog

    def answered(dialog):
        change(dialog)
        return QDialog.Accepted

    monkeypatch.setattr(CanopenSettingsDialog, "exec", answered)


def lines(window) -> int:
    return len(window.log.toPlainText().splitlines())


def test_ok_with_nothing_changed_says_nothing(window, monkeypatch):
    answer_settings(monkeypatch)
    before = lines(window)
    window.canopen_view._open_settings()
    assert lines(window) == before


def test_ok_says_only_what_changed(window, monkeypatch):
    from pycangui.ui import canopen_settings

    old = canopen_settings.CanopenSettings()
    new = canopen_settings.CanopenSettings(sync_period_ms=50, heartbeat_timeouts={5: 2000})
    assert len(canopen_settings.changes(old, new)) == 2
    assert canopen_settings.changes(new, new) == []

    answer_settings(monkeypatch, lambda dialog: dialog.retries.setValue(4))
    before = lines(window)
    window.canopen_view._open_settings()
    assert lines(window) == before + 1, "one line, for the one change"


def test_a_running_sync_is_offered_a_restart_when_its_settings_change(window, monkeypatch):
    from pycangui.ui import canopen_view

    view = window.canopen_view
    started = []
    monkeypatch.setattr(window.canopen, "start_sync", lambda *args: started.append(args))
    monkeypatch.setattr(window.canopen, "stop_sync", lambda: None)
    view.sync_btn.setChecked(True)
    asked = []
    monkeypatch.setattr(
        canopen_view.messages,
        "question",
        lambda *a, **k: asked.append(a[1]) or canopen_view.messages.Button.Yes,
    )

    answer_settings(monkeypatch, lambda dialog: dialog.retries.setValue(4))
    view._open_settings()
    assert not asked, "nothing SYNC uses changed"

    answer_settings(monkeypatch, lambda dialog: dialog.sync_period.setValue(20))
    view._open_settings()
    assert asked and started[-1] == (0.02,), "restarted at the new period"


def test_remove_node_takes_the_row_and_works_on_a_lost_one(window):
    view = window.canopen_view
    view.on_node_seen(9, "added by hand")
    view.on_node_lost(9)
    view.nodes.setCurrentItem(view._node_item(9))
    assert view.remove_btn.isEnabled(), "a lost node most of all"
    assert not view._node_buttons[0].isEnabled()
    view.remove_btn.click()
    assert view._node_item(9) is None and 9 not in view._lost


def test_a_refused_button_says_so_in_a_box(window, monkeypatch):
    """The Event Log can be behind the window the button was pressed in."""
    from pycangui.ui import canopen_view

    view = window.canopen_view
    boxes = []
    for kind in ("warning", "information"):
        monkeypatch.setattr(canopen_view.messages, kind, lambda *a, **k: boxes.append(a[1]))
    view._on_stored_eds_failed(5, "this node does not keep its EDS")
    view._add_node()  # nothing connected
    view.on_node_seen(9, "added by hand")
    view.nodes.setCurrentItem(view._node_item(9))
    view._read_all()  # no EDS, so nothing to read
    assert len(boxes) == 3
    assert view.read_all_btn.isVisibleTo(view), "and nothing was started"


def test_read_all_ends_with_how_many_failed_and_why(window, monkeypatch):
    """Counted, not a warning each: a real device has hundreds it lacks."""
    from pycangui.ui import canopen_view

    view = window.canopen_view
    boxes = []
    monkeypatch.setattr(canopen_view.messages, "warning", lambda *a, **k: boxes.append(a[2]))
    view._reading_node, view._read_failures = 5, []
    for sub in range(3):
        view.on_sdo_result(5, 0x2000, sub, None, "abort 0x06020000, object does not exist")
    view.on_sdo_result(5, 0x2001, 0, None, "SdoCommunicationError: No SDO response")
    view._on_read_finished(5, 10, 10, False)
    assert len(boxes) == 1
    assert "3 x abort 0x06020000" in boxes[0] and "1 x SdoCommunicationError" in boxes[0]

    view._reading_node, view._read_failures = 5, []
    view._on_read_finished(5, 10, 10, False)
    assert len(boxes) == 1, "nothing failed, nothing to say"


def test_totals_are_the_commonest_first():
    from pycangui.canopen.manager import totals_text

    lines = totals_text(["b", "a", "b", "b", "a", "c"]).splitlines()
    assert [line.split(" x ")[0].strip() for line in lines] == ["3", "2", "1"]


def test_a_domain_read_is_in_the_canopen_log(stack):
    """A block is read a piece at a time, which the log did not see."""
    manager, _demo, _tmp = stack
    records = []
    manager.sdo_logged.connect(records.append)
    manager.sdo_read(5, 0x1021, 0)  # the demo device's own EDS, a DOMAIN
    wait_until(lambda: any(r.index == 0x1021 and r.data for r in records))


def test_an_nmt_frame_too_short_to_be_one_is_ignored_and_said_once(on_a_bus):
    """A device sending 0x000 with no data made the library fail on every frame."""
    said = []
    on_a_bus.message.connect(lambda text, level: said.append(text))
    heard = []
    on_a_bus.network.subscribe(0, lambda can_id, data, ts: heard.append(bytes(data)))
    for _ in range(20):
        on_a_bus.network.notify(0, bytearray(), 0.0)
    on_a_bus.network.notify(0, bytearray(b"\x81\x05"), 0.0)
    assert heard == [b"\x81\x05"], "only the real command reaches the library"
    assert len(said) == 1


def test_failures_are_listed_by_name_under_their_reason(stack):
    manager, _demo, _tmp = stack
    text = manager.failures_text(
        5,
        [
            (0x2001, 0, "abort 0x06010002, attempt to write a read only object"),
            (0x6041, 0, "abort 0x06010002, attempt to write a read only object"),
            (0x1017, 0, "SdoCommunicationError: No SDO response received"),
        ],
    )
    lines = text.splitlines()
    assert lines[0].strip().endswith("(2):") and "0x06010002" in lines[0]
    assert lines[1].strip().startswith("2001:00") and "Statusword" in lines[2]
    assert lines[3].strip().endswith("(1):")


def mapping_settled(manager, node_id=5) -> None:
    """Wait for the mapping read that loading the EDS started.

    It runs on the manager's worker and rebuilds every PDO map as it goes. A
    test that takes a map and changes it before that has finished has its
    changes undone under it -- the map back at full length, the frame the test
    made then too short for it, and nothing decoded.
    """
    wait_until(lambda: node_id in manager._mapping_read)


def tpdo1(manager):
    mapping_settled(manager)
    manager.load_pdos_from_eds(5)
    return next(m for m in manager.node(5).tpdo.map.values() if m.name.startswith("TxPDO1"))


def test_a_pdo_shorter_than_its_mapping_says_so_once_with_the_bits(stack):
    """'Mismatch between expected and actual data size', a line a frame, said
    nothing about which PDO or by how much."""
    manager, _demo, _tmp = stack
    said, decoded = [], []
    manager.message.connect(lambda text, level: said.append((level, text)))
    manager.pdo_update.connect(lambda *args: decoded.append(args))
    pdo = tpdo1(manager)
    pdo.data = bytearray(6)
    for _ in range(5):
        manager._on_pdo(5, pdo)
    shorts = [text for _level, text in said if "received 48 bits" in text]
    assert len(shorts) == 1 and "expected 64 bits" in shorts[0]
    assert not decoded, "not decoded from a frame that is too short"


def test_an_object_mapped_with_fewer_bits_than_its_type_is_decoded_as_mapped(stack):
    """A device maps the low bits of a wider object to fit the frame -- eight of
    an INTEGER16, say. The library read the type's full size and failed at the
    end of the frame, on every frame."""
    manager, _demo, _tmp = stack
    said, decoded = [], []
    manager.message.connect(lambda text, level: said.append(text))
    manager.pdo_update.connect(lambda node, name, values: decoded.append(values))
    pdo = tpdo1(manager)
    last = pdo.map[-1]  # a 32-bit object, mapped here as 16
    last.length = 16
    pdo.length -= 16
    pdo.data = bytearray([0, 0, 0, 0, 0x34, 0x12])
    said.clear()  # what loading the mapping said is not what is being asked
    manager._on_pdo(5, pdo)
    manager._on_pdo(5, pdo)
    assert decoded[0][last.name] == 0x1234, "the bits that were sent"
    assert len(decoded[0]) == len(pdo.map), "and nothing left out"
    assert not said, "flagged when the mapping is read, not on every frame"


def test_a_signed_object_mapped_short_keeps_its_sign(stack):
    from canopen.objectdictionary import datatypes

    manager, _demo, _tmp = stack
    values = []
    manager.pdo_update.connect(lambda node, name, got: values.append(got))
    pdo = tpdo1(manager)
    first = pdo.map[0]
    first.od.data_type = datatypes.INTEGER16
    first.length = 8  # the low eight bits of a signed sixteen
    for var in pdo.map[1:]:
        var.offset -= 8
    pdo.length -= 8
    pdo.data = bytearray([0xFE, 0, 0, 0, 0, 0, 0])
    manager._on_pdo(5, pdo)
    assert values[0][first.name] == -2


def test_an_rpdo_object_mapped_short_is_sent_as_its_mapped_bits(stack):
    """The library wrote the type's full size, over what follows and past the frame."""
    manager, _demo, _tmp = stack
    mapping_settled(manager)
    manager.load_pdos_from_eds(5)
    number, pdo = next(iter(manager.node(5).rpdo.map.items()))
    var = pdo.map[0]  # a 16-bit object, mapped here as its low 8 bits
    var.length = 8
    pdo.length = 8
    pdo.data = bytearray(1)
    cob_id, data = manager.encode_rpdo(5, number, {var.name: 0x1FE})
    assert cob_id == pdo.cob_id and data == bytes([0xFE]), "one byte, the low eight bits"


def test_a_mapped_size_that_is_not_the_types_is_flagged_when_the_mapping_is_read(stack):
    """The EDS says INTEGER16 and the mapping 8 bits: one of them is wrong
    about the object, or part of it is mapped, and either way it is worth
    knowing before trusting the number."""
    manager, _demo, _tmp = stack
    warned = []
    manager.message.connect(lambda text, level: level == "warning" and warned.append(text))
    pdo = tpdo1(manager)
    assert manager.mapping_size_mismatches(5) == [], "the demo device agrees with its EDS"
    manager.flag_mapping_sizes(5)
    assert not warned

    pdo.map[-1].length = 16  # a 32-bit object
    found = manager.mapping_size_mismatches(5)
    assert len(found) == 1 and pdo.map[-1].name in found[0]
    manager.flag_mapping_sizes(5)
    manager.flag_mapping_sizes(5)
    assert len(warned) == 1, "said once for the same mapping"
    manager.flag_mapping_sizes(5, again=True)
    assert len(warned) == 2, "and again when a read was asked for"
