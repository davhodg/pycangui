# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""PDO configuration, DCF save/apply, store/restore and SYNC, against the demo node."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

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
        if time.monotonic() > deadline:
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


def test_read_all_can_be_stopped(stack):
    manager, _demo, _tmp = stack
    finished = []
    manager.read_finished.connect(lambda *args: finished.append(args))
    manager.sdo_result.connect(lambda *_args: manager.stop_batch())  # after the first
    manager.read_many(5, [(0x1000, 0)] * 50)
    wait_until(lambda: finished)
    _node, done, total, stopped = finished[0]
    assert stopped and done < total == 50


def test_a_stopped_dcf_save_writes_no_file(stack, monkeypatch):
    manager, _demo, tmp = stack
    finished = []
    manager.dcf_finished.connect(lambda *args: finished.append(args))
    manager.dcf_progress.connect(lambda *_args: manager.stop_batch())
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
    assert view.remove_node_btn.isEnabled(), "a lost node most of all"
    assert not view._node_buttons[0].isEnabled()
    view.remove_node_btn.click()
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
