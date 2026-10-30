# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Checks of an EDS or DCF as it is loaded: each fault found, at its object,
and the file loaded all the same."""

import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.canopen import eds_check
from pycangui.canopen.eds_check import check, number
from pycangui.canopen.manager import CanopenManager
from pycangui.core.bus import BusManager
from pycangui.core.events import WARNING

DEMO = resources.path("demo.eds")
#: The demo's speed demand and controlword, up to their default values.
SPEED = "ParameterName=Speed demand\nObjectType=0x7\nDataType=0x0003\nAccessType=rw\n"
CONTROL = "ParameterName=Controlword\nObjectType=0x7\nDataType=0x0006\nAccessType=rww\n"


def broken(tmp_path, *changes, name="broken.eds"):
    """The demo EDS with each (old, new) text replaced once."""
    text = DEMO.read_text(encoding="utf-8")
    for old, new in changes:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def places(path):
    return {f.where for f in check(path)}


def section(name, *lines):
    return "[" + name + "]\n" + "\n".join(lines) + "\n"


def test_the_demo_file_is_clean():
    assert check(DEMO) == []


@pytest.mark.parametrize(
    "text, node_id, value",
    [
        ("0x180", 1, 0x180),
        ("$NODEID+0x180", 5, 0x185),
        ("0x200 + $NODEID", 5, 0x205),
        ("10", 1, 10),
        ("010", 1, 8),
        ("-5", 1, -5),
        ("soon", 1, None),
        ("", 1, None),
    ],
)
def test_numbers_as_an_eds_writes_them(text, node_id, value):
    assert number(text, node_id) == value


def test_a_missing_mandatory_object(tmp_path):
    path = broken(
        tmp_path,
        (
            "SupportedObjects=3\n1=0x1000\n2=0x1001\n3=0x1018",
            "SupportedObjects=2\n1=0x1000\n2=0x1018",
        ),
        (section("1001", "ParameterName=Error register"), ""),
    )
    found = places(path)
    assert "0x1001" in found


def test_a_count_that_does_not_match_its_list(tmp_path):
    path = broken(tmp_path, ("SupportedObjects=3\n1=0x1000", "SupportedObjects=4\n1=0x1000"))
    assert places(path) == {"[MandatoryObjects]"}


def test_an_object_listed_with_no_section(tmp_path):
    path = broken(
        tmp_path,
        (
            "SupportedObjects=2\n1=0x2000\n2=0x2001",
            "SupportedObjects=3\n1=0x2000\n2=0x2001\n3=0x2002",
        ),
    )
    assert places(path) == {"[ManufacturerObjects]"}


def test_device_info_counting_pdos_the_file_does_not_have(tmp_path):
    path = broken(tmp_path, ("NrOfTXPDO=1", "NrOfTXPDO=4"))
    assert places(path) == {"[DeviceInfo]"}


def test_a_tpdo_mapping_an_object_that_is_not_there(tmp_path):
    path = broken(tmp_path, ("DefaultValue=0x20000220", "DefaultValue=0x2FFF0020"))
    assert places(path) == {"0x1A00 sub 3"}


def test_an_rpdo_mapping_an_object_that_cannot_be_written(tmp_path):
    path = broken(tmp_path, ("DefaultValue=0x20010010", "DefaultValue=0x60410010"))
    assert places(path) == {"0x1600 sub 1"}


def test_a_mapping_of_an_object_not_marked_mappable(tmp_path):
    path = broken(
        tmp_path,
        (
            "DataType=0x0003\nAccessType=rw\nDefaultValue=0\nPDOMapping=1",
            "DataType=0x0003\nAccessType=rw\nDefaultValue=0\nPDOMapping=0",
        ),
    )
    assert places(path) == {"0x1600 sub 1"}


def test_a_mapping_longer_than_the_object(tmp_path):
    path = broken(tmp_path, ("DefaultValue=0x20010010", "DefaultValue=0x20010020"))
    assert places(path) == {"0x1600 sub 1"}


def test_more_bits_than_a_pdo_carries(tmp_path):
    # 16 + 16 + 32 already; make the last 64 bits long, of a 64-bit object.
    path = broken(
        tmp_path,
        ("DefaultValue=0x20000220", "DefaultValue=0x20000240"),
        (
            "ParameterName=Odometer\nObjectType=0x7\nDataType=0x0007",
            "ParameterName=Odometer\nObjectType=0x7\nDataType=0x001B",
        ),
    )
    assert places(path) == {"0x1A00"}


def test_dummies_may_be_mapped_into_an_rpdo(tmp_path):
    path = broken(tmp_path, ("DefaultValue=0x20010010", "DefaultValue=0x00060010"))
    assert check(path) == []


def test_limits_the_wrong_way_round_and_a_default_outside_them(tmp_path):
    path = broken(
        tmp_path,
        (SPEED + "DefaultValue=0", SPEED + "DefaultValue=500\nLowLimit=-100\nHighLimit=100"),
        (CONTROL + "DefaultValue=0", CONTROL + "DefaultValue=0\nLowLimit=10\nHighLimit=5"),
    )
    assert places(path) == {"0x2001", "0x6040"}


def test_a_default_that_does_not_fit_its_type(tmp_path):
    path = broken(
        tmp_path,
        ("DefaultValue=255\nPDOMapping=0\n\n[1600]", "DefaultValue=256\nPDOMapping=0\n\n[1600]"),
    )
    assert places(path) == {"0x1400 sub 2"}


def test_a_negative_default_written_as_twos_complement_is_fine(tmp_path):
    path = broken(
        tmp_path,
        (SPEED + "DefaultValue=0", SPEED + "DefaultValue=0xFFFF"),
    )
    assert check(path) == []


def test_a_cob_id_no_frame_can_carry(tmp_path):
    path = broken(tmp_path, ("DefaultValue=$NODEID+0x180", "DefaultValue=$NODEID+0x7F0"))
    assert places(path) == {"0x1800 sub 1"}, "fine for node 1, too big for 127"


def test_a_value_that_is_not_a_number(tmp_path):
    path = broken(tmp_path, ("DefaultValue=0x0237", "DefaultValue=soon"))
    assert places(path) == {"0x6041"}


def test_an_unreadable_file_is_left_to_the_loader(tmp_path):
    assert check(tmp_path / "none.eds") == []


# --- in the CANopen manager ----------------------------------------------------------
def wait_until(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


@pytest.fixture
def manager(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    bus = BusManager()
    manager = CanopenManager(bus)
    manager.read_pdos_when_found = False
    bus.connect_bus("virtual", "vcan_eds_check", 500000, False)
    yield manager
    bus.disconnect_bus()
    manager.shutdown()


def load(manager, path):
    loaded = []
    manager.eds_loaded.connect(lambda *a: loaded.append(a))
    manager.load_eds(5, str(path))
    wait_until(lambda: loaded)


def test_findings_are_said_once_and_the_file_is_loaded_anyway(manager, tmp_path):
    path = broken(tmp_path, ("NrOfTXPDO=1", "NrOfTXPDO=4"))
    said = []
    manager.message.connect(lambda text, level: said.append(level))
    load(manager, path)
    assert manager.node(5) is not None and len(manager.node(5).object_dictionary)
    assert said.count(WARNING) == 1
    load(manager, path)
    assert said.count(WARNING) == 1, "not again for the same file"


def test_a_clean_file_says_nothing(manager):
    said = []
    manager.message.connect(lambda text, level: said.append(level))
    load(manager, DEMO)
    assert WARNING not in said


def test_a_long_list_is_cut_short(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(
        eds_check, "check", lambda _p: [eds_check.Finding(f"0x{i:04X}", "x") for i in range(30)]
    )
    said = []
    manager.message.connect(lambda text, level: said.append(text))
    load(manager, DEMO)
    listed = [s for s in said if "0x0000" in s]
    assert listed and "0x0010" not in listed[0] and "0x0009" in listed[0]


def test_a_file_opened_with_no_node_is_checked_once_too(manager, tmp_path):
    path = broken(tmp_path, ("NrOfTXPDO=1", "NrOfTXPDO=4"))
    said = []
    manager.message.connect(lambda text, level: said.append(level))
    manager.check_file(str(path))
    manager.check_file(str(path))
    assert said.count(WARNING) == 1
