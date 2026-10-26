# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Drive the CANopen manager against the demo device on the virtual bus."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import NodeIdentity, eds_extras, find_eds, load_od
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager


def wait_until(pred, timeout=3.0):
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
    bus.connect_bus("virtual", "vcan_test", 500000, False)
    demo = demo_device(bus, kinds=["canopen_device"])
    yield bus, manager, demo
    bus.disconnect_bus()
    manager.shutdown()


def test_discovery_identify_eds_sdo_pdo(stack):
    _bus, manager, _demo = stack
    seen, identities, loaded, sdo, pdo = [], [], [], [], []
    manager.node_seen.connect(lambda n, s: seen.append((n, s)))
    manager.identified.connect(identities.append)
    manager.eds_loaded.connect(lambda n, p, name: loaded.append((n, name)))
    manager.sdo_result.connect(lambda n, i, s, v, e: sdo.append((i, s, v, e)))
    manager.pdo_update.connect(lambda n, name, values: pdo.append(values))

    wait_until(lambda: seen)  # heartbeat
    assert seen[0] == (5, "PRE-OPERATIONAL")

    manager.identify(5)
    wait_until(lambda: identities)
    ident = identities[0]
    assert (ident.vendor_id, ident.product_code, ident.revision) == (0, 0x1234, 0x10002)
    assert ident.key == "00000000:00001234:00010002"

    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    assert loaded[0] == (5, "Demo Drive")

    manager.sdo_read(5, 0x6041, 0)
    wait_until(lambda: sdo)
    assert sdo[-1] == (0x6041, 0, 0x0237, None)

    manager.sdo_write(5, 0x2001, 0, "-40")
    wait_until(lambda: len(sdo) == 2)
    assert sdo[-1] == (0x2001, 0, -40, None)

    manager.nmt(5, "OPERATIONAL")
    wait_until(lambda: any(s == "OPERATIONAL" for _, s in seen))
    wait_until(lambda: any(v.get("Measurements.Motor speed", 0) < 0 for v in pdo))

    manager.sdo_read(5, 0x9999, 0)  # not implemented: error, no crash
    wait_until(lambda: len(sdo) == 3)
    assert sdo[-1][3] is not None


def test_find_eds_matches_device_info(tmp_path):
    # The demo's vendor ID is 0, which nobody is assigned: found all the same.
    ident = NodeIdentity(5, vendor_id=0, product_code=0x1234, revision=0x10002)
    assert find_eds(ident, [resources.path("")]).name == "demo.eds"
    assert find_eds(NodeIdentity(5, 0, 0x9999), [resources.path("")]) is None
    assert find_eds(NodeIdentity(5), [resources.path("")]) is None
    (tmp_path / "junk.eds").write_text("not an eds")
    assert find_eds(ident, [tmp_path]) is None


def test_an_eds_that_is_not_utf8_is_still_matched(tmp_path):
    """Vendor tools often write Windows-1252; one such file stopped the search."""
    ident = NodeIdentity(5, vendor_id=0x42, product_code=0x1234)
    (tmp_path / "a_other.eds").write_bytes(
        b"[FileInfo]\nDescription=Motor at 40\xb0C\n[DeviceInfo]\nVendorNumber=0x7\n"
    )
    (tmp_path / "b_drive.eds").write_bytes(
        b"[FileInfo]\nDescription=Range 0-5\xb5A\n"
        b"[DeviceInfo]\nVendorNumber=0x42\nProductNumber=0x1234\n"
    )
    assert find_eds(ident, [tmp_path]).name == "b_drive.eds"


def test_an_eds_in_the_windows_code_page_loads_with_its_characters(tmp_path):
    """0x96 is an en dash in Windows-1252, and no UTF-8 at all."""
    demo = resources.path("demo.eds").read_bytes()
    path = tmp_path / "drive.eds"
    path.write_bytes(demo.replace(b"=Error register", b"=Error \x96 register"))

    assert load_od(path, 5)[0x1001].name == "Error \u2013 register"
    assert eds_extras(path) is not None


def test_a_utf8_eds_keeps_its_characters_too(tmp_path):
    demo = resources.path("demo.eds").read_bytes()
    path = tmp_path / "drive.eds"
    path.write_bytes(demo.replace(b"=Error register", "=Error \u00b0C".encode()))
    assert load_od(path, 5)[0x1001].name == "Error \u00b0C"


# --- what a refused DCF write is reported as --------------------------------------
def test_an_abort_is_reported_as_a_code_and_its_meaning():
    """The code goes to the maker; the meaning tells the person at the
    machine whether it is a read-only object or a value out of range."""
    import canopen

    from pycangui.canopen.manager import _reason

    assert _reason(canopen.SdoAbortedError(0x06010002)) == (
        "abort 0x06010002, Attempt to write a read only object"
    )
    assert _reason(canopen.SdoAbortedError(0x06090030)).startswith("abort 0x06090030, Value range")
    assert _reason(canopen.SdoAbortedError(0x0BAD0000)) == "abort 0x0BAD0000", "no invented meaning"
    assert _reason(TimeoutError("no response")) == "TimeoutError: no response"


def test_failures_are_grouped_by_reason_in_the_order_they_happened():
    """A DCF that goes wrong usually goes wrong the same way two hundred
    times, and two hundred identical lines say it worse than one does."""
    from pycangui.canopen.manager import _by_reason

    grouped = _by_reason(
        [(0x2001, 0, "read only"), (0x1017, 0, "out of range"), (0x2003, 1, "read only")]
    )
    assert list(grouped) == ["read only", "out of range"]
    assert grouped["read only"] == [(0x2001, 0), (0x2003, 1)]


def test_a_tpdo_is_found_by_the_name_it_is_decoded_under(stack):
    """What lets a database that describes the same frame take its place."""
    _bus, manager, _demo = stack
    loaded, names = [], []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.pdo_update.connect(lambda _n, name, _values: names.append(name))
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    manager.nmt(5, "OPERATIONAL")
    wait_until(lambda: names)
    assert manager.pdo_cob_id(5, names[0]) == 0x185, "demo.dbc's DriveStatus"
    assert manager.pdo_direction(5, names[0]) == "TPDO"
    assert manager.pdo_cob_id(5, "no such PDO") is None
    assert manager.pdo_cob_id(99, names[0]) is None, "nor a node nobody knows"


def _loaded(manager):
    loaded = []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.load_eds(5, str(resources.path("demo.eds")))
    wait_until(lambda: loaded)
    wait_until(lambda: manager.read_from_node(5) == (True, True))
    QCoreApplication.processEvents()


def test_an_rpdo_another_master_sends_is_decoded(stack):
    """pycangui may only be listening, with another master sending the node
    its RPDOs: what the node is told is decoded as well as what it says."""
    import can

    _bus, manager, _demo = stack
    _loaded(manager)
    updates = []
    manager.pdo_update.connect(lambda n, name, values: updates.append((n, name, values)))
    other_master = can.Bus(interface="virtual", channel="vcan_test")
    try:
        other_master.send(can.Message(arbitration_id=0x205, data=b"\x2c\x01", is_extended_id=False))
        wait_until(lambda: any(name.startswith("Rx") for _n, name, _v in updates))
    finally:
        other_master.shutdown()
    node, name, values = next(u for u in updates if u[1].startswith("Rx"))
    assert node == 5 and list(values.values()) == [300]
    assert manager.pdo_direction(5, name) == "RPDO"
    assert manager.pdo_cob_id(5, name) == 0x205, "found by name, as a TPDO is"


def test_an_rpdo_pycangui_sends_itself_is_decoded_from_its_echo(stack):
    """CAN Transmit sends by the same mapping; the frame comes back from the
    bus as anyone else's would, so a monitor shows what was on the wire."""
    bus, manager, _demo = stack
    _loaded(manager)
    updates = []
    manager.pdo_update.connect(lambda n, name, values: updates.append((name, values)))
    cob_id, data = manager.encode_rpdo(5, 1, {"Speed demand": -2})
    bus.send(cob_id, data)
    wait_until(lambda: any(name.startswith("Rx") for name, _v in updates))
    assert next(v for name, v in updates if name.startswith("Rx")) == {"Speed demand": -2}


def test_each_rpdo_is_decoded_once_however_often_it_is_read(stack):
    _bus, manager, _demo = stack
    _loaded(manager)
    node = manager.node(5)
    rpdo1 = node.rpdo.map[1]
    before = len(rpdo1.callbacks)
    assert before == 1
    manager._decode_pdos(5, node)
    assert len(rpdo1.callbacks) == 1, "a second read does not decode every frame twice"
