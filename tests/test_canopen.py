"""Drive the CANopen manager against the demo device on the virtual bus."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import NodeIdentity, find_eds
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.demo import DemoDevice


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def stack(app):
    bus = BusManager()
    manager = CanopenManager(bus)
    bus.connect_bus("virtual", "vcan_test", 500000, False)
    demo = DemoDevice("vcan_test")
    yield bus, manager, demo
    demo.stop()
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
    assert (ident.vendor_id, ident.product_code, ident.revision) == (0x42, 0x1234, 0x10002)
    assert ident.key == "00000042:00001234:00010002"

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
    ident = NodeIdentity(5, vendor_id=0x42, product_code=0x1234, revision=0x10002)
    assert find_eds(ident, [resources.path("")]).name == "demo.eds"
    assert find_eds(NodeIdentity(5, 0x42, 0x9999), [resources.path("")]) is None
    assert find_eds(NodeIdentity(5), [resources.path("")]) is None
    (tmp_path / "junk.eds").write_text("not an eds")
    assert find_eds(ident, [tmp_path]) is None
