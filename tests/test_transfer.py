# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware transfer: reading the files, and the services that move them.

The ECU here is a recording fake rather than the demo device.  What these
tests are about is the shape of the conversation -- how many RequestDownloads
a file with a gap in it causes, what the block sequence counter does after
255, how big a block is allowed to be -- and none of that is visible from the
outside of a working transfer.
"""

from types import SimpleNamespace

import bincopy
import pytest
from PySide6.QtWidgets import QMessageBox

from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.uds import images
from pycangui.uds.manager import UdsManager
from pycangui.ui.uds_view import UdsView


# --- a fake ECU -----------------------------------------------------------------------
def reply(**fields):
    return SimpleNamespace(service_data=SimpleNamespace(**fields))


class FakeEcu:
    """Records what it was asked to do, and hands back plausible answers."""

    def __init__(self, max_length: int = 8, memory: bytes = b"") -> None:
        self.max_length = max_length
        self.calls: list[tuple] = []
        self.written = bytearray()
        self._out = bytes(memory)
        self.file_size = len(memory)
        self.dir_length = 0
        self.file_position = 0
        self.on_block = None  # called with the block number, to interrupt

    # the four services a transfer is made of
    def request_download(self, memory_location, dfi=None):
        self.location = memory_location
        self.calls.append(("download", memory_location.address, memory_location.memorysize))
        return reply(max_length=self.max_length)

    def request_upload(self, memory_location, dfi=None):
        self.calls.append(("upload", memory_location.address, memory_location.memorysize))
        return reply(max_length=self.max_length)

    def transfer_data(self, sequence_number, data=None):
        self.calls.append(("data", sequence_number, data))
        if self.on_block is not None:
            self.on_block(len([c for c in self.calls if c[0] == "data"]))
        if data is None:  # the ECU is the one sending
            block, self._out = self._out[: self.max_length - 2], self._out[self.max_length - 2 :]
            return reply(sequence_number_echo=sequence_number, parameter_records=block)
        self.written += data
        return reply(sequence_number_echo=sequence_number, parameter_records=b"")

    def routine_control(self, routine_id, control, data=None):
        self.calls.append(("routine", routine_id, control, bytes(data or b"")))
        return reply(routine_status_record=b"")

    def request_transfer_exit(self, data=None):
        self.calls.append(("exit",))
        return reply(parameter_records=b"")

    def request_file_transfer(self, moop, path, dfi=None, filesize=None):
        self.calls.append(("file", moop, path, filesize.uncompressed if filesize else None))
        return reply(
            moop_echo=moop,
            max_length=self.max_length,
            dfi=dfi,
            filesize=SimpleNamespace(uncompressed=self.file_size, compressed=self.file_size),
            dirinfo_length=self.dir_length,
            fileposition=self.file_position,
        )

    def close(self):
        pass


class Immediate:
    """A worker that is not one: the job runs where it was submitted."""

    def submit(self, fn, callback):
        try:
            callback(fn(), None)
        except Exception as exc:  # matches Worker.run
            callback(None, f"{type(exc).__name__}: {exc}")

    def stop(self):
        pass


@pytest.fixture
def manager(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    m = UdsManager(BusManager(), Hooks(ctx), ctx)
    m._worker = Immediate()
    yield m
    m.client = None


@pytest.fixture
def images_dir(tmp_path):
    def write(name, *segments):
        """segments: (address, data) pairs."""
        binfile = bincopy.BinFile()
        for address, data in segments:
            binfile.add_binary(data, address=address)
        path = tmp_path / name
        if name.endswith(".bin"):
            path.write_bytes(b"".join(data for _, data in segments))
        elif name.endswith(".hex"):
            path.write_text(binfile.as_ihex())
        else:
            path.write_text(binfile.as_srec())
        return str(path)

    return write


# --- reading files --------------------------------------------------------------------
def test_hex_and_srec_carry_their_own_address(images_dir):
    for name in ("a.hex", "a.s19"):
        image = images.read(images_dir(name, (0x8000, bytes(range(32)))))
        assert image.address == 0x8000
        assert image.size == 32
        assert image.segments[0].data == bytes(range(32))


def test_the_format_is_read_from_the_contents_not_the_name(images_dir):
    """A tool that writes S-records into a .hex should still work."""
    path = images_dir("misnamed.hex", (0x100, b"\x01\x02"))
    open(path, "w").write(bincopy.BinFile.__call__ and "")  # truncate
    binfile = bincopy.BinFile()
    binfile.add_binary(b"\x01\x02", address=0x100)
    open(path, "w").write(binfile.as_srec())
    assert images.read(path).format == "Motorola S-record"


def test_a_raw_binary_has_to_be_told_where_it_goes(images_dir):
    path = images_dir("a.bin", (0, b"\x01\x02\x03"))
    with pytest.raises(images.ImageError, match="start address"):
        images.read(path)
    assert images.read(path, 0x2000).address == 0x2000
    assert images.looks_binary(path), "and the pane can tell before it reads it"


def test_gaps_stay_gaps(images_dir):
    """Padding them would write bytes the file never contained."""
    image = images.read(images_dir("split.hex", (0x1000, b"\xaa" * 4), (0x9000, b"\xbb" * 4)))
    assert [s.address for s in image.segments] == [0x1000, 0x9000]
    assert image.size == 8, "the hole is not counted, because it is not sent"
    assert "2 segments" in image.summary()


def test_an_empty_file_says_so(tmp_path):
    path = tmp_path / "nothing.hex"
    path.write_text("")
    with pytest.raises(images.ImageError, match="empty"):
        images.read(str(path))


# --- writing files --------------------------------------------------------------------
def test_the_name_chooses_the_format(tmp_path):
    data = bytes(range(64))
    for name, first in (("out.hex", ":"), ("out.s19", "S")):
        path = str(tmp_path / name)
        images.write(path, 0x400, data)
        assert open(path).read()[0] == first
        assert images.read(path).segments[0].data == data, "and it reads back the same"
    path = str(tmp_path / "out.bin")
    assert images.write(path, 0x400, data) == "raw binary"
    assert open(path, "rb").read() == data, "exactly what came off the bus, nothing added"


# --- download -------------------------------------------------------------------------
def test_download_is_one_request_per_segment(manager, images_dir):
    """A file with a hole gets a RequestDownload each side of it."""
    manager.client = ecu = FakeEcu(max_length=6)
    image = images.read(images_dir("split.hex", (0x1000, b"\xaa" * 8), (0x9000, b"\xbb" * 4)))
    lines = []
    manager.result.connect(lines.append)

    manager.download(image)
    starts = [c for c in ecu.calls if c[0] == "download"]
    assert starts == [("download", 0x1000, 8), ("download", 0x9000, 4)]
    assert [c[0] for c in ecu.calls].count("exit") == 2, "each one is finished before the next"
    assert ecu.written == b"\xaa" * 8 + b"\xbb" * 4
    assert lines[-1].startswith("Download complete: 12 bytes")


def test_the_block_size_leaves_room_for_the_service_id_and_counter(manager, images_dir):
    """maxNumberOfBlockLength counts the whole request, not just the data.

    Sending max_length bytes per block is the classic way to get a download
    that works until the ECU answers 0x31.
    """
    manager.client = ecu = FakeEcu(max_length=8)
    manager.download(images.read(images_dir("a.hex", (0, bytes(24)))))
    blocks = [c[2] for c in ecu.calls if c[0] == "data"]
    assert {len(b) for b in blocks} == {6}, "8 reported, 2 for the header, 6 of data"
    assert len(blocks) == 4


def test_a_block_size_can_be_forced(manager, images_dir):
    manager.client = ecu = FakeEcu(max_length=1024)
    manager.download(images.read(images_dir("a.hex", (0, bytes(20)))), block_size=5)
    assert [len(c[2]) for c in ecu.calls if c[0] == "data"] == [5, 5, 5, 5]


def test_the_sequence_counter_wraps_from_255_to_zero(manager, images_dir):
    """ISO 14229: the first block is 1, and 0xFF is followed by 0, not by 1."""
    manager.client = ecu = FakeEcu(max_length=3)  # one data byte per block
    manager.download(images.read(images_dir("a.hex", (0, bytes(300)))))
    sequence = [c[1] for c in ecu.calls if c[0] == "data"]
    assert sequence[:3] == [1, 2, 3]
    assert sequence[254:257] == [255, 0, 1]


def test_download_can_be_stopped_between_blocks(manager, images_dir):
    manager.client = ecu = FakeEcu(max_length=3)
    lines = []
    manager.result.connect(lines.append)
    ecu.on_block = lambda n: manager.cancel_transfer() if n == 4 else None

    manager.download(images.read(images_dir("a.hex", (0, bytes(50)))))
    assert len(ecu.written) == 4, "the block already promised is finished, then it stops"
    assert "cancelled after 4 of 50 bytes" in lines[-1]
    assert ("exit",) not in ecu.calls, "a cancelled transfer is not a finished one"


def test_an_image_that_starts_at_address_zero_can_be_sent(manager, images_dir):
    """udsoncan sizes the address field from the address's bit length, which
    is zero bits for the number zero, and then refuses the zero it produced.

    Flash starting at 0 is an ordinary thing for a bootloader to be given.
    """
    manager.client = ecu = FakeEcu()
    lines = []
    manager.result.connect(lines.append)
    manager.download(images.read(images_dir("a.hex", (0, bytes(8)))))
    assert ("download", 0, 8) in ecu.calls
    assert lines[-1].startswith("Download complete")
    assert ecu.location.address_format == 8, "the narrowest that can be written, not none"


def test_a_fixed_address_width_can_be_demanded(manager, images_dir):
    """Bootloaders that want 32 bits answer anything narrower with NRC 0x13."""
    manager.client = ecu = FakeEcu()
    manager.download(images.read(images_dir("a.hex", (0x100, bytes(8)))), width=32)
    assert ecu.location.address_format == 32
    assert ecu.location.memorysize_format == 32


def test_a_transfer_will_not_start_on_top_of_another(manager):
    manager.client = FakeEcu()
    manager._busy = True
    lines = []
    manager.result.connect(lines.append)
    manager.upload("ignored", 0, 4)
    assert "already running" in lines[-1]


# --- upload ---------------------------------------------------------------------------
def test_upload_writes_back_exactly_what_arrived(manager, tmp_path):
    manager.client = FakeEcu(max_length=6, memory=bytes(range(40)))
    out = str(tmp_path / "read_back.hex")
    lines = []
    manager.result.connect(lines.append)

    manager.upload(out, 0x4000, 40)
    assert images.read(out).address == 0x4000
    assert images.read(out).segments[0].data == bytes(range(40))
    assert "Upload complete: 40 bytes" in lines[-1]


def test_an_ecu_that_stops_early_is_reported_not_padded(manager, tmp_path):
    manager.client = FakeEcu(max_length=6, memory=bytes(8))
    lines = []
    manager.result.connect(lines.append)
    manager.upload(str(tmp_path / "short.bin"), 0, 40)
    assert "stopped sending after 8 of 40 bytes" in lines[-1]


# --- file transfer (0x38) -------------------------------------------------------------
def test_a_file_transfer_names_the_file_and_needs_no_address(manager, tmp_path):
    manager.client = ecu = FakeEcu(max_length=10)
    local = tmp_path / "app.bin"
    local.write_bytes(bytes(range(20)))

    manager.file_transfer(1, "/fs/app.bin", str(local))
    assert ("file", 1, "/fs/app.bin", 20) in ecu.calls, "the path is the address"
    assert ecu.written == bytes(range(20))
    assert not any(c[0] in ("download", "upload") for c in ecu.calls)


def test_reading_a_file_off_the_ecu_saves_it_as_it_came(manager, tmp_path):
    manager.client = FakeEcu(max_length=6, memory=b"log contents here")
    out = tmp_path / "fetched.txt"
    manager.file_transfer(4, "/fs/log.txt", str(out))
    assert out.read_bytes() == b"log contents here"


def test_delete_asks_for_nothing_else(manager):
    manager.client = ecu = FakeEcu()
    lines = []
    manager.result.connect(lines.append)
    manager.file_transfer(2, "/fs/old.bin")
    assert [c[0] for c in ecu.calls] == ["file"], "no blocks, no transfer exit"
    assert lines[-1] == "Deleted /fs/old.bin"


def test_resume_carries_on_from_where_the_ecu_got_to(manager, tmp_path):
    manager.client = ecu = FakeEcu(max_length=10)
    ecu.file_position = 12
    local = tmp_path / "app.bin"
    local.write_bytes(bytes(range(20)))

    manager.file_transfer(6, "/fs/app.bin", str(local))
    assert ecu.written == bytes(range(20))[12:], "only the part it has not got"


def test_a_directory_listing_is_read_rather_than_saved(manager):
    manager.client = ecu = FakeEcu(max_length=6)
    ecu.dir_length = 12
    ecu._out = b"app.bin\ncal.b"
    lines = []
    manager.result.connect(lines.append)
    manager.file_transfer(5, "/fs")
    assert "/fs:" in lines[-1] and "app.bin" in lines[-1]


# --- the pane -------------------------------------------------------------------------
@pytest.fixture
def view(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    m = UdsManager(BusManager(), Hooks(ctx), ctx)
    m._worker = Immediate()
    v = UdsView(m, ctx)
    yield v
    m.client = None


def test_the_button_says_which_service_it_will_use(view):
    assert view.start.text() == "Download"
    view.operation.setCurrentIndex(view.operation.findData("upload"))
    assert view.start.text() == "Upload"
    view.operation.setCurrentIndex(view.operation.findData(1))
    assert view.start.text() == "Add file"


def test_a_file_transfer_greys_out_the_address(view):
    view.operation.setCurrentIndex(view.operation.findData(1))
    assert not view.address.isEnabled() and not view.byte_count.isEnabled()
    assert view.ecu_path.isEnabled(), "the path is how a file transfer is addressed"
    view.operation.setCurrentIndex(view.operation.findData("upload"))
    assert view.address.isEnabled() and view.byte_count.isEnabled()
    assert not view.ecu_path.isEnabled()


def test_choosing_a_hex_file_fills_the_address_in_and_locks_it(view, images_dir):
    view.local.setText(images_dir("a.hex", (0x8000, bytes(16))))
    view._reload_image()
    assert view.address.text() == "8000"
    assert view.byte_count.text() == "10"
    assert not view.address.isEnabled(), "the file is right; a typed number could only be wrong"
    assert "Intel HEX" in view.output.toPlainText()


def test_choosing_a_binary_asks_for_an_address(view, images_dir):
    view.local.setText(images_dir("a.bin", (0, bytes(16))))
    view._reload_image()
    assert view.address.isEnabled()
    assert view._image is None, "nothing to send until it is told where"
    assert "start address" in view.output.toPlainText()

    view.address.setText("2000")
    view._reload_image()
    assert view._image is not None and view._image.address == 0x2000


def test_writing_to_the_ecu_is_asked_about_first(view, images_dir, monkeypatch):
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: (asked.append(box.text() + " " + box.informativeText()), QMessageBox.Cancel)[1],
    )
    view.manager.client = ecu = FakeEcu()
    view.local.setText(images_dir("a.hex", (0x8000, bytes(16))))
    view._reload_image()

    view.start.click()
    assert asked and "8000" in asked[0], "say which image, and where it is going"
    assert not ecu.calls, "cancel means nothing was sent"


def test_reading_from_the_ecu_is_not_worth_a_question(view, tmp_path, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: pytest.fail("an upload changes nothing"))
    view.manager.client = FakeEcu(memory=bytes(8))
    view.local.setText(str(tmp_path / "out.bin"))
    view.operation.setCurrentIndex(view.operation.findData("upload"))
    view.address.setText("0")
    view.byte_count.setText("8")
    view.start.click()
    assert (tmp_path / "out.bin").read_bytes() == bytes(8)


def test_the_progress_bar_says_what_is_happening(view):
    view.manager.progress.emit("Download", 40, 100)
    assert view.bar.value() == 40 and view.bar.maximum() == 100
    assert "40 of 100 bytes" in view.bar.format()


def test_cancel_is_only_offered_while_something_is_running(view):
    assert not view.stop.isEnabled()
    view.manager.transferring.emit(True)
    assert view.stop.isEnabled() and not view.start.isEnabled()
    view.manager.transferring.emit(False)
    assert not view.stop.isEnabled() and view.start.isEnabled()


# --- the routines around a download ---------------------------------------------------
def test_nothing_is_erased_unless_it_is_asked_for(manager, images_dir):
    manager.client = ecu = FakeEcu()
    manager.download(images.read(images_dir("a.hex", (0x8000, bytes(8)))))
    assert not [c for c in ecu.calls if c[0] == "routine"]


def test_every_segment_is_erased_before_any_is_written(manager, images_dir):
    """Two segments can share a flash block, and erasing between them would
    take the first one back out again."""
    manager.client = ecu = FakeEcu()
    image = images.read(images_dir("split.hex", (0x1000, bytes(4)), (0x9000, bytes(4))))
    manager.download(image, erase=True)

    kinds = [c[0] for c in ecu.calls]
    assert kinds[:2] == ["routine", "routine"], "both erases, then the first download"
    assert kinds[2] == "download"
    assert [c[1] for c in ecu.calls if c[0] == "routine"] == [0xFF00, 0xFF00]
    assert all(c[2] == 1 for c in ecu.calls if c[0] == "routine"), "startRoutine"


def test_the_erase_is_told_which_addresses_to_erase(manager, images_dir):
    manager.client = ecu = FakeEcu()
    manager.download(images.read(images_dir("a.hex", (0x8000, bytes(0x10)))), erase=True)
    record = next(c[3] for c in ecu.calls if c[0] == "routine")
    # 0x12: the length takes one byte and the address two, then each of them.
    assert record == bytes([0x12, 0x80, 0x00, 0x10]), "format byte, address, length"


def test_a_forced_width_reaches_the_erase_too(manager, images_dir):
    """An ECU that wants 32-bit addresses wants them in the routine as well."""
    manager.client = ecu = FakeEcu()
    manager.download(images.read(images_dir("a.hex", (0x8000, bytes(4)))), erase=True, width=32)
    record = next(c[3] for c in ecu.calls if c[0] == "routine")
    assert record[0] == 0x44 and len(record) == 9


def test_the_check_routine_runs_after_each_segment(manager, images_dir):
    manager.client = ecu = FakeEcu()
    image = images.read(images_dir("split.hex", (0x1000, bytes(4)), (0x9000, bytes(4))))
    lines = []
    manager.result.connect(lines.append)
    manager.download(image, check=0x0202)

    kinds = [c[0] for c in ecu.calls]
    assert kinds.index("routine") > kinds.index("exit"), "after the transfer, not before it"
    assert [c[1] for c in ecu.calls if c[0] == "routine"] == [0x0202, 0x0202]
    assert any("Check memory" in line for line in lines), "named, not just numbered"


def test_the_standard_routines_are_named(manager):
    assert manager.routine_label(0xFF00) == "FF00 (Erase memory)"
    assert manager.routine_label(0xFF01) == "FF01 (Check programming dependencies)"
    assert manager.routine_label(0x0202) == "0202 (Check memory)", "a convention, but a known one"
    assert manager.routine_label(0x1234) == "1234", "nothing to say beyond the number"


def test_erase_and_check_are_only_offered_for_a_download(view):
    assert view.erase.isEnabled() and view.check.isEnabled()
    assert not view.check_routine.isEnabled(), "nothing to configure until it is wanted"
    view.check.setChecked(True)
    assert view.check_routine.isEnabled()

    view.operation.setCurrentIndex(view.operation.findData("upload"))
    assert not view.erase.isEnabled(), "an upload writes nothing, so erases nothing"
    assert not view.check.isEnabled()


def test_the_pane_passes_them_on(view, images_dir, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Yes)
    view.manager.client = ecu = FakeEcu()
    view.local.setText(images_dir("a.hex", (0x8000, bytes(8))))
    view._reload_image()
    view.erase.setChecked(True)
    view.check.setChecked(True)
    view.check_routine.setText("0301")

    view.start.click()
    assert [c[1] for c in ecu.calls if c[0] == "routine"] == [0xFF00, 0x0301]


def test_the_question_says_the_memory_will_be_erased(view, images_dir, monkeypatch):
    asked = []
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda box: (asked.append(box.text() + " " + box.informativeText()), QMessageBox.Cancel)[1],
    )
    view.manager.client = FakeEcu()
    view.local.setText(images_dir("a.hex", (0x8000, bytes(8))))
    view._reload_image()
    view.erase.setChecked(True)
    view.start.click()
    assert "erased first" in asked[0]
