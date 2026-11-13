# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Flashing, uploading and checksums over XCP and CCP, against the demo slaves."""

import time
import zlib

import pytest
from PySide6.QtCore import QCoreApplication, Qt

from pycangui import resources
from pycangui.core import workspaces
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.uds import images
from pycangui.uds.images import Image, Segment
from pycangui.xcp import checksum
from pycangui.xcp import transfer as plans
from pycangui.xcp.a2l import A2l, Area
from pycangui.xcp.manager import (
    DOWNLOAD,
    VERIFY_NONE,
    VERIFY_READ_BACK,
    FlashOptions,
    XcpManager,
)

APPLICATION, HEADER = 0x4000, 0x7000


# --- checksums ----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (checksum.CRC_32, 0xCBF43926),
        (checksum.CRC_16, 0xBB3D),
        (checksum.CRC_16_CITT, 0x29B1),
        (checksum.ADD_11, sum(b"123456789") & 0xFF),
        (checksum.ADD_12, sum(b"123456789")),
    ],
)
def test_checksums_are_the_standard_ones(kind, expected):
    """The usual check string, against each algorithm's published answer."""
    assert checksum.compute(kind, b"123456789") == expected


def test_word_sums_read_words_in_the_slaves_order():
    data = bytes([0x01, 0x02, 0x03, 0x04])
    assert checksum.compute(checksum.ADD_22, data, big_endian=False) == 0x0201 + 0x0403
    assert checksum.compute(checksum.ADD_22, data, big_endian=True) == 0x0102 + 0x0304
    assert checksum.compute(checksum.ADD_44, data, big_endian=True) == 0x01020304


def test_a_user_defined_checksum_cannot_be_worked_out_here():
    assert checksum.compute(checksum.USER_DEFINED, b"abc") is None


# --- areas from the A2L ---------------------------------------------------------------------
def test_sectors_give_the_areas_and_their_order():
    a2l = A2l.load(str(resources.path("demo.a2l")))
    areas = a2l.flash_areas()
    assert [(a.name, a.address, a.size) for a in areas] == [
        ("application", APPLICATION, 0x3000),
        ("header", HEADER, 0x1000),
    ]
    header = areas[1]
    assert header.clear_order < areas[0].clear_order, "the header is erased first"
    assert header.program_order > areas[0].program_order, "and written last"


def test_without_sectors_the_flash_segments_are_used_in_address_order():
    a2l = A2l.parse(
        """
        /begin MODULE m ""
        /begin MOD_PAR ""
          /begin MEMORY_SEGMENT b "" CODE FLASH INTERN 0x9000 0x100 -1 -1 -1 -1 -1
          /end MEMORY_SEGMENT
          /begin MEMORY_SEGMENT r "" DATA RAM INTERN 0x1000 0x100 -1 -1 -1 -1 -1
          /end MEMORY_SEGMENT
          /begin MEMORY_SEGMENT x "" RESERVED FLASH INTERN 0x5000 0x100 -1 -1 -1 -1 -1
          /end MEMORY_SEGMENT
          /begin MEMORY_SEGMENT a "" CODE FLASH INTERN 0x8000 0x100 -1 -1 -1 -1 -1
          /end MEMORY_SEGMENT
        /end MOD_PAR
        /end MODULE
        """
    )
    assert len(a2l.memory_segments) == 4
    areas = a2l.flash_areas()
    assert [a.name for a in areas] == ["a", "b"], "RAM and reserved flash left out"
    assert [a.program_order for a in areas] == [0, 1]


# --- the plan -------------------------------------------------------------------------------
AREAS = [
    Area("application", APPLICATION, 0x3000, clear_order=1, program_order=0),
    Area("header", HEADER, 0x1000, clear_order=0, program_order=1),
]


def test_a_plan_follows_the_areas_order():
    segments = [Segment(HEADER, b"\x11" * 16), Segment(APPLICATION, b"\x22" * 32)]
    plan = plans.make(segments, AREAS)
    assert [a.name for a in plan.clears] == ["header", "application"]
    assert [(r.area, r.address) for r in plan.runs] == [
        ("application", APPLICATION),
        ("header", HEADER),
    ]
    assert plan.size == 48 and not plan.outside


def test_bytes_in_no_area_are_left_out_unless_asked_for():
    segments = [Segment(HEADER - 8, b"\x33" * 16)]  # half in the application, half in the header
    only_header = [AREAS[1]]
    plan = plans.make(segments, only_header)
    assert [(r.address, len(r.data)) for r in plan.runs] == [(HEADER, 8)]
    assert plan.outside == ((HEADER - 8, 8),)

    forced = plans.make(segments, only_header, outside_too=True)
    assert not forced.outside
    assert sorted((r.address, len(r.data)) for r in forced.runs) == [(HEADER - 8, 8), (HEADER, 8)]
    assert len(forced.clears) == 2, "what is written is erased first, the extra area as well"


def test_an_area_the_file_does_not_touch_is_not_erased():
    plan = plans.make([Segment(APPLICATION, b"\x44" * 4)], AREAS)
    assert [a.name for a in plan.clears] == ["application"]
    assert [a.name for a in plan.untouched] == ["header"]


def test_with_no_areas_the_file_is_flashed_as_it_is():
    segments = [Segment(0x100, b"\x01" * 4), Segment(0x200, b"\x02" * 4)]
    plan = plans.make(segments, [])
    assert [(a.address, a.size) for a in plan.clears] == [(0x100, 4), (0x200, 4)]


# --- against the demo slaves -----------------------------------------------------------------
def wait_until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline and not pred():
            raise AssertionError("timed out")
        time.sleep(0.005)


def _stack(app, tmp_path, monkeypatch, demo_device, kind, ids, station=None):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    hooks = Hooks(ctx)
    bus = BusManager()
    manager = XcpManager(bus, hooks, SignalHub(), ctx)
    manager.set_component("ccp-builtin" if kind == "ccp_slave" else "xcp-builtin")
    bus.connect_bus("virtual", f"vcan_{kind}_transfer", 500000, False)
    demo = demo_device(bus, kinds=[kind])
    manager.set_ids(*ids, False)
    if station is not None:
        manager.set_station(station)
    hook = "def compute_key(resource, seed, *, ctx):\n    return bytes(b ^ 0xFF for b in seed)\n"
    (workspaces.hooks_dir() / "xcp.py").write_text(hook)
    hooks.reload()
    manager.connect_slave()
    wait_until(lambda: manager.is_connected)
    return bus, manager, demo[kind].state


@pytest.fixture
def xcp(app, tmp_path, monkeypatch, demo_device):
    bus, manager, slave = _stack(
        app, tmp_path, monkeypatch, demo_device, "xcp_slave", (0x7A0, 0x7A1)
    )
    yield manager, slave
    manager.shutdown()
    bus.disconnect_bus()


@pytest.fixture
def ccp(app, tmp_path, monkeypatch, demo_device):
    bus, manager, slave = _stack(
        app, tmp_path, monkeypatch, demo_device, "ccp_slave", (0x7B0, 0x7B1), station=1
    )
    yield manager, slave
    manager.shutdown()
    bus.disconnect_bus()


def _run(manager, start) -> bool:
    outcome = []
    manager.transferred.connect(outcome.append)
    start()
    wait_until(lambda: outcome)
    return outcome[0]


def _firmware() -> Image:
    application = bytes(range(256)) * 3 + b"\x5a" * 3  # not a whole number of frames
    header = b"VALID" + zlib.crc32(application).to_bytes(4, "little")
    return Image(
        "fw.hex", "Intel HEX", (Segment(APPLICATION, application), Segment(HEADER, header))
    )


def _areas():
    return A2l.load(str(resources.path("demo.a2l"))).flash_areas()


def test_flashing_over_xcp_writes_the_file_and_checks_it(xcp):
    manager, slave = xcp
    image = _firmware()
    assert _run(manager, lambda: manager.flash(image, _areas(), FlashOptions()))
    for segment in image.segments:
        assert slave.memory[segment.address : segment.address + len(segment)] == segment.data
    assert slave.memory[HEADER + len(image.segments[1])] == 0xFF, "the rest of the area erased"


def test_flashing_needs_the_pgm_key(xcp):
    manager, slave = xcp
    (workspaces.hooks_dir() / "xcp.py").write_text("")
    manager._hooks.reload()
    assert not _run(manager, lambda: manager.flash(_firmware(), _areas(), FlashOptions()))
    assert slave.memory[APPLICATION] == 0xFF, "nothing written"


def test_without_erasing_first_a_read_back_finds_the_difference(xcp):
    manager, slave = xcp
    # Not erased: zeros where the file has 0x10 to 0x13, which flash cannot turn back.
    slave.memory[APPLICATION + 0x10 : APPLICATION + 0x14] = bytes(4)
    options = FlashOptions(clear=False, verify=VERIFY_READ_BACK)
    assert not _run(manager, lambda: manager.flash(_firmware(), _areas(), options))


def test_reset_after_programming_disconnects(xcp):
    manager, slave = xcp
    assert _run(manager, lambda: manager.flash(_firmware(), _areas(), FlashOptions(reset=True)))
    wait_until(lambda: not manager.is_connected)
    assert not slave.connected and not slave.programming


def test_downloading_writes_ram_with_download(xcp):
    """For memory that is written like RAM: no erase, no PROGRAM, the CAL key."""
    manager, slave = xcp
    image = Image("cal.bin", "raw binary", (Segment(0x2100, bytes(range(40))),))
    ram = [Area("calibration", 0x2000, 0x1000)]
    options = FlashOptions(write_with=DOWNLOAD, verify=VERIFY_READ_BACK)
    assert _run(manager, lambda: manager.flash(image, ram, options))
    assert slave.memory[0x2100:0x2128] == bytes(range(40))


def test_uploading_reads_the_areas_into_one_file(xcp, tmp_path):
    manager, slave = xcp
    slave.memory[APPLICATION : APPLICATION + 8] = b"ABCDEFGH"
    path = tmp_path / "read.hex"
    assert _run(manager, lambda: manager.upload(str(path), _areas()))
    back = images.read(str(path))
    # The two sectors are next to each other, so the file has them as one.
    assert [(s.address, len(s)) for s in back.segments] == [(APPLICATION, 0x4000)]
    assert back.segments[0].data[:8] == b"ABCDEFGH"

    apart = [Area("a", 0x1000, 0x10), Area("b", APPLICATION, 0x10)]
    assert _run(manager, lambda: manager.upload(str(path), apart))
    assert [(s.address, len(s)) for s in images.read(str(path)).segments] == [
        (0x1000, 0x10),
        (APPLICATION, 0x10),
    ]


def test_a_raw_binary_holds_only_one_area(xcp, tmp_path):
    manager, _slave = xcp
    path = tmp_path / "read.bin"
    assert not _run(manager, lambda: manager.upload(str(path), _areas()))
    assert not path.exists()


def test_a_checksum_against_the_file_tells_flashed_from_not(xcp):
    manager, _slave = xcp
    image = _firmware()
    assert not _run(manager, lambda: manager.build_checksums(_areas(), image))
    assert _run(manager, lambda: manager.flash(image, _areas(), FlashOptions(verify=VERIFY_NONE)))
    assert _run(manager, lambda: manager.build_checksums(_areas(), image))


def test_flashing_over_ccp_with_the_ecus_checksum_chosen(ccp):
    manager, slave = ccp
    image = _firmware()
    options = FlashOptions(checksum_kind=checksum.ADD_12)
    assert _run(manager, lambda: manager.flash(image, _areas(), options))
    for segment in image.segments:
        assert slave.memory[segment.address : segment.address + len(segment)] == segment.data
    wrong = FlashOptions(checksum_kind=checksum.CRC_32)
    assert not _run(manager, lambda: manager.flash(image, _areas(), wrong)), "a different sum"


def test_the_a2l_says_which_checksum_each_protocol_uses():
    a2l = A2l.load(str(resources.path("demo.a2l")))
    assert a2l.checksums["XCP"].kind == checksum.CRC_32
    assert a2l.checksums["CCP"].kind == checksum.ADD_12, "CCP's procedure numbers are XCP's"
    assert a2l.checksums["CCP"].max_block == 0x100


def test_a_makers_own_procedure_is_kept_but_not_worked_out():
    a2l = A2l.parse(
        """
        /begin MODULE m ""
        /begin IF_DATA ASAP1B_CCP
          /begin TP_BLOB 0x201 0x204 1 2 1 1
            /begin CHECKSUM_PARAM 0xC001 0xFFFFFFFF CHECKSUM_CALCULATION_ACTIVE_PAGE
            /end CHECKSUM_PARAM
          /end TP_BLOB
        /end IF_DATA
        /end MODULE
        """
    )
    info = a2l.checksums["CCP"]
    assert info.code == 0xC001 and info.kind is None


def test_ccp_compares_with_the_a2ls_checksum_without_being_told(ccp):
    """CCP's answer says nothing of the algorithm; the A2L's CHECKSUM_PARAM does."""
    manager, _slave = ccp
    manager.load_a2l(str(resources.path("demo.a2l")))
    image = _firmware()
    assert _run(manager, lambda: manager.flash(image, _areas(), FlashOptions()))
    assert _run(manager, lambda: manager.build_checksums(_areas(), image))
    image_off = Image("x.hex", "Intel HEX", (Segment(APPLICATION, b"\x01" * 0x303),))
    assert not _run(manager, lambda: manager.build_checksums(_areas(), image_off))


def test_checksums_are_asked_in_blocks_no_bigger_than_the_a2l_allows(xcp):
    manager, _slave = xcp
    manager.load_a2l(str(resources.path("demo.a2l")))  # MAX_BLOCK_SIZE 0x100
    asked = []
    real = manager.engine.checksum
    manager.engine.checksum = lambda a, n, **kw: asked.append(n) or real(a, n, **kw)
    assert _run(manager, lambda: manager.flash(_firmware(), _areas(), FlashOptions()))
    assert asked == [0x100, 0x100, 0x100, 0x3, 9], "the application's 0x303 bytes in four"


def test_ccp_has_no_reset(ccp):
    manager, _slave = ccp
    assert not _run(manager, lambda: manager.flash(_firmware(), _areas(), FlashOptions(reset=True)))
    assert manager.is_connected


def test_cancel_stops_between_pieces(xcp):
    manager, slave = xcp
    big = Image("big.hex", "Intel HEX", (Segment(APPLICATION, b"\x00" * 0x3000),))
    outcome = []
    manager.transferred.connect(outcome.append)
    manager.progress.connect(lambda *_: manager.cancel_transfer())
    manager.flash(big, _areas(), FlashOptions(verify=VERIFY_NONE))
    wait_until(lambda: outcome)
    assert outcome == [False]
    assert slave.memory[APPLICATION + 0x2FFF] == 0xFF, "the end never written"


# --- the tab -------------------------------------------------------------------------------
class _Agree:
    """Confirmations that say yes, and count how often they were asked."""

    def __init__(self):
        self.asked = 0

    def ask(self, *_args):
        self.asked += 1
        return True


def _tab(manager, tmp_path, image=None):
    from pycangui.ui.xcp_view import XcpView

    manager.load_a2l(str(resources.path("demo.a2l")))
    agree = _Agree()
    view = XcpView(manager, manager._ctx, agree)
    tab = view.transfer
    if image is not None:
        path = tmp_path / "fw.hex"
        images.write_segments(str(path), list(image.segments))
        tab.local.setText(str(path))
        tab._reload_image()
    return view, tab, agree


def _rows(tab):
    return [tab.table.item(r, 0).text() for r in range(tab.table.rowCount())]


def test_the_pane_has_a_transfer_tab_with_the_a2ls_areas(xcp, tmp_path):
    manager, _slave = xcp
    view, tab, _agree = _tab(manager, tmp_path, _firmware())
    assert [view.tabs.tabText(i) for i in range(view.tabs.count())] == ["Parameters", "Transfer"]
    assert _rows(tab) == ["application", "header"]
    in_file = [int(tab.table.item(r, 5).text(), 16) for r in range(2)]
    assert in_file == [len(s) for s in _firmware().segments]
    assert tab.start.isEnabled(), "connected, and nothing running"


def test_program_from_the_tab_asks_then_flashes(xcp, tmp_path):
    manager, slave = xcp
    image = _firmware()
    _view, tab, agree = _tab(manager, tmp_path, image)
    assert _run(manager, tab._start)
    assert agree.asked == 1
    for segment in image.segments:
        assert slave.memory[segment.address : segment.address + len(segment)] == segment.data


def test_an_unticked_area_is_left_alone_and_the_table_is_kept(xcp, tmp_path):
    manager, slave = xcp
    _view, tab, _agree = _tab(manager, tmp_path, _firmware())
    tab.table.item(1, 0).setCheckState(Qt.Unchecked)  # the header
    assert [a.name for a in tab.areas()] == ["application"]
    assert _run(manager, tab._start)
    assert slave.memory[HEADER] == 0xFF, "the header neither erased nor written"

    _again, tab2, _ = _tab(manager, tmp_path)
    assert [a.name for a in tab2.areas()] == ["application"], "kept for next time"
    tab2._areas_from_a2l()
    assert [a.name for a in tab2.areas()] == ["application", "header"], "until From A2L"


def test_an_area_the_a2l_does_not_list_can_be_added(xcp, tmp_path):
    manager, slave = xcp
    image = Image("x.hex", "Intel HEX", (Segment(0x2800, b"\x12\x34"),))  # RAM, in no sector
    _view, tab, _agree = _tab(manager, tmp_path, image)
    tab.operation.setCurrentIndex(tab.operation.findData(DOWNLOAD))
    tab._add_area()
    row = tab.table.rowCount() - 1
    tab.table.item(row, 1).setText("2800")
    tab.table.item(row, 2).setText("10")
    assert _run(manager, tab._start)
    assert slave.memory[0x2800:0x2802] == b"\x12\x34"


def test_from_file_makes_the_files_segments_the_areas(xcp, tmp_path):
    manager, _slave = xcp
    _view, tab, _agree = _tab(manager, tmp_path, _firmware())
    tab._areas_from_file()
    areas = tab.areas()
    assert [(a.address, a.size) for a in areas] == [
        (s.address, len(s)) for s in _firmware().segments
    ]


def test_ccp_has_no_reset_to_offer(ccp, tmp_path):
    manager, _slave = ccp
    _view, tab, _agree = _tab(manager, tmp_path, _firmware())
    assert not tab.reset.isEnabled()
