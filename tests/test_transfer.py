# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware transfer: reading the files, and the services that move them.

The ECU here is a recording fake rather than the demo device. What these
tests are about is the shape of the conversation -- how many RequestDownloads
a file with a gap in it causes, what the block sequence counter does after
255, how big a block is allowed to be -- and none of that is visible from the
outside of a working transfer.
"""

from types import SimpleNamespace

import bincopy
import pytest
from PySide6.QtWidgets import QGroupBox, QMessageBox
from udsoncan.connections import BaseConnection

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
    # One ECU on the wire here: every service goes to it, none to all ECUs.
    m.config.functional = []
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


def test_intel_hex_lines_that_are_not_records_are_passed_over(images_dir):
    """Tools put comments and titles in; only a line starting with ':' is data."""
    path = images_dir("commented.hex", (0x8000, bytes(range(32))))
    records = open(path).read()
    with open(path, "w") as out:
        out.write("; built by some tool\n; version 1.2\n\n" + records + "; the end\n")

    image = images.read(path)

    assert image.format == "Intel HEX", "not taken for raw binary by its first line"
    assert (image.address, image.segments[0].data) == (0x8000, bytes(range(32)))
    assert image.ignored == 3
    assert "3 lines not starting with ':' ignored" in image.summary()


def test_a_clean_hex_file_says_nothing_about_ignoring(images_dir):
    image = images.read(images_dir("a.hex", (0x8000, b"\x01\x02")))
    assert image.ignored == 0 and "ignored" not in image.summary()


def test_a_text_file_with_no_records_is_not_taken_for_intel_hex(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("; nothing here\n:not a record\n")
    with pytest.raises(images.ImageError, match="start address"):
        images.read(str(path))


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
    from pycangui.uds.sequence import Values
    from pycangui.ui import uds_sequence

    uds_sequence.save(view.ctx.settings, Values(check_routine=0x0301))

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


# --- refusing an image that is not for this ECU ----------------------------------------
def hooked(manager, monkeypatch, answer):
    real = manager._hooks.call

    def call(module, name, *args, **kwargs):
        if module == "uds" and name == "before_download":
            return answer(*args)
        return real(module, name, *args, **kwargs)

    monkeypatch.setattr(manager._hooks, "call", call)


def test_a_hook_can_refuse_an_image_before_anything_is_written(manager, images_dir, monkeypatch):
    """Writing the right file to the wrong controller is the most expensive
    mistake this tool can make, and nothing in the file says which
    controller it is for."""
    manager.client = ecu = FakeEcu(max_length=8)
    hooked(manager, monkeypatch, lambda _image, _client: "not built for this controller")
    lines = []
    manager.result.connect(lines.append)

    manager.download(images.read(images_dir("a.hex", (0x1000, bytes(16)))), erase=True)

    assert ecu.calls == [], "nothing erased and nothing written"
    assert "REFUSED" in lines[-1]
    assert "not built for this controller" in lines[-1]
    assert "before_download" in lines[-1], "and which hook said so"


def test_the_hook_is_given_the_image_and_the_open_client(manager, images_dir, monkeypatch):
    manager.client = ecu = FakeEcu(max_length=8)
    seen: list = []
    hooked(manager, monkeypatch, lambda image, client: seen.append((image, client)))

    manager.download(images.read(images_dir("b.hex", (0x2000, bytes(8)))))

    assert seen, "it was asked"
    image, client = seen[0]
    assert image.address == 0x2000 and image.size == 8
    assert client is ecu, "the session it would be writing through"


def test_saying_nothing_lets_the_download_go_ahead(manager, images_dir, monkeypatch):
    manager.client = ecu = FakeEcu(max_length=8)
    hooked(manager, monkeypatch, lambda _image, _client: None)

    manager.download(images.read(images_dir("c.hex", (0x3000, bytes(8)))))

    assert ecu.written == bytes(8)


# --- tester present on a bus that will not take it --------------------------------------
class RefusingEcu(FakeEcu):
    def tester_present(self):
        from pycangui.uds.transport import FrameRefusedError

        raise FrameRefusedError("the adapter would not send the request: queue is full")


def test_tester_present_stops_and_says_why_when_the_adapter_refuses_it(manager):
    """Left ticking, it fills the adapter's queue again every couple of seconds."""
    manager.client = RefusingEcu()
    stopped, results = [], []
    manager.tester_present_stopped.connect(stopped.append)
    manager.result.connect(results.append)
    manager.set_tester_present(True)
    assert manager._tp_timer.isActive()

    manager._tester_present_tick()

    assert not manager._tp_timer.isActive()
    assert stopped and "queue is full" in stopped[0]
    assert not results, "said once, as the reason it stopped"


def test_the_pane_unticks_the_box_and_logs_it_as_an_error(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    from pycangui.core.events import ERROR

    ctx = Context(log=print)
    posted = []
    ctx.events.posted.connect(lambda text, level: posted.append((level, text)))
    m = UdsManager(BusManager(), Hooks(ctx), ctx)
    view = UdsView(m, ctx)
    view.tp.setChecked(True)

    m.tester_present_stopped.emit("Tester present stopped: queue is full")

    assert not view.tp.isChecked()
    assert "queue is full" in view.output.toPlainText()
    assert (ERROR, "Tester present stopped: queue is full") in posted


# --- an ECU that stays busy for many times P2* -------------------------------------------
def test_a_request_is_waited_for_as_long_as_the_ecu_says_pending(manager):
    """Each 0x78 starts P2* again. An overall cap of P2* + 1 s cut a flash erase
    off a second before its positive response, and the download with it."""
    import time as clock

    from pycangui.core.components import register_component
    from pycangui.uds import UdsConfig
    from pycangui.uds.transport import IsoTpTransport

    @register_component("isotp", "busy-ecu", "answers 0x78 for a while, then yes")
    class BusyEcu(IsoTpTransport):
        def open(self):
            self.queue = []

        def close(self):
            pass

        def send(self, payload):
            now = clock.monotonic()
            pending = bytes([0x7F, payload[0], 0x78])
            # Pending every 0.25 s for 1.6 s: P2* is 0.4 s, so the old cap
            # was 1.4 s and gave up before the answer.
            self.queue = [(now + 0.25 * n, pending) for n in range(7)]
            self.queue.append((now + 1.6, bytes([payload[0] + 0x40]) + payload[1:4] + b"\x00"))

        def recv(self, timeout):
            if not self.queue:
                clock.sleep(timeout)
                return None
            due, data = self.queue[0]
            wait = due - clock.monotonic()
            if wait > timeout:
                clock.sleep(timeout)
                return None
            clock.sleep(max(wait, 0))
            self.queue.pop(0)
            return data

    manager._bus.connect_bus("virtual", "vcan_busy_ecu", 500_000, False)
    manager.component_name = "busy-ecu"
    results = []
    manager.result.connect(results.append)
    manager.open(UdsConfig(p2_timeout_s=0.1, p2_star_timeout_s=0.4))
    try:
        manager.routine(1, 0xFF00, b"\x34\x0c\x02\x00\x00\x06\x00\x00")
    finally:
        manager.close()
        manager._bus.disconnect_bus()

    answer = next(line for line in results if line.startswith("Routine"))
    assert "timeout" not in answer.lower(), answer
    assert "FF00" in answer


def test_a_download_request_is_logged_as_it_goes_out(manager, images_dir):
    """Logged only once accepted, a request the ECU never answered left
    "Download: timeout" with nothing to say what had been asked."""
    from udsoncan.exceptions import TimeoutException

    class Silent(FakeEcu):
        def request_download(self, memory_location, dfi=None):
            raise TimeoutException("no answer")

    manager.client = Silent()
    lines = []
    manager.result.connect(lines.append)

    manager.download(images.read(images_dir("a.hex", (0x1000, b"\xaa" * 8))))

    asked = lines.index("RequestDownload 00001000: 8 bytes")
    assert "timeout" in lines[asked + 1]
    assert not any("accepted" in line for line in lines)


def test_the_security_level_is_remembered(view):
    """Every other choice in the pane came back; the level went back to 1."""
    view.level.setValue(4)
    again = UdsView(view.manager, view.ctx)
    assert again.level.value() == 4
    assert "07" in again.level_pair.text(), "and the bytes beside it follow the level"


def test_a_timeout_says_which_request_it_was_waiting_on(manager, images_dir):
    """ "Download: timeout" alone said nothing about where it had got to."""
    from udsoncan.exceptions import TimeoutException

    class StopsAnswering(FakeEcu):
        def transfer_data(self, sequence_number, data=None):
            if sequence_number == 3:
                raise TimeoutException("no answer")
            return super().transfer_data(sequence_number, data)

    manager.client = StopsAnswering(max_length=6)
    lines = []
    manager.result.connect(lines.append)

    manager.download(images.read(images_dir("a.hex", (0x1000, bytes(20)))))

    assert lines[-1] == "Download: timeout (no response to TransferData block 3 of 5)"


def test_the_timing_choice_is_remembered_and_used(view):
    view.timing.setCurrentIndex(view.timing.findData("at least"))
    view.p2.setValue(800)
    assert (view.manager.config.timing, view.manager.config.p2_timeout_s) == ("at least", 0.8)

    again = UdsView(view.manager, view.ctx)
    assert again.timing.currentData() == "at least"
    assert again.p2.value() == 800


# --- ECU control ---------------------------------------------------------------------------
class Answering(BaseConnection):
    """A udsoncan connection that records each request and answers it positively."""

    def __init__(self) -> None:
        super().__init__("test")
        self.sent: list[bytes] = []
        self._reply: bytes | None = None
        self._open = False

    def open(self):
        self._open = True
        return self

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open

    def specific_send(self, payload: bytes) -> None:
        self.sent.append(bytes(payload))
        self._reply = bytes([payload[0] + 0x40]) + bytes(payload[1:2])

    def specific_wait_frame(self, timeout: float = 2) -> bytes | None:
        reply, self._reply = self._reply, None
        return reply

    def empty_rxqueue(self) -> None:
        pass


@pytest.fixture
def ecu_on_the_wire(manager):
    from udsoncan.client import Client

    wire = Answering()
    manager.client = Client(wire)
    manager.client.open()
    return wire


def test_communication_control_sends_the_control_and_the_messages(manager, ecu_on_the_wire):
    lines = []
    manager.result.connect(lines.append)
    manager.communication_control(1, 1)  # enable Rx, disable Tx, normal messages
    assert ecu_on_the_wire.sent[-1] == bytes([0x28, 0x01, 0x01])


def test_a_bitrate_change_is_verified_before_it_is_made(manager, ecu_on_the_wire):
    lines = []
    manager.result.connect(lines.append)
    manager.change_bitrate(500_000)
    # 0x12 is the fixed identifier ISO 14229-1 gives 500 kbit/s on CAN.
    assert ecu_on_the_wire.sent == [bytes([0x87, 0x01, 0x12]), bytes([0x87, 0x03])]


def test_a_bitrate_can_be_checked_without_being_changed(manager, ecu_on_the_wire):
    manager.check_bitrate(500_000)
    assert ecu_on_the_wire.sent == [bytes([0x87, 0x01, 0x12])], "the verify, and no transition"


@pytest.mark.parametrize("button", ["_communication_control", "_change_bitrate"])
def test_ecu_control_asks_before_it_changes_the_bus(view, monkeypatch, button):
    sent = []
    monkeypatch.setattr(view.manager, "communication_control", lambda *a: sent.append(a))
    monkeypatch.setattr(view.manager, "change_bitrate", lambda *a: sent.append(a))
    monkeypatch.setattr(view.confirm, "ask", lambda *a: False)
    getattr(view, button)()
    assert sent == [], "declined, so nothing goes out"
    monkeypatch.setattr(view.confirm, "ask", lambda *a: True)
    getattr(view, button)()
    assert len(sent) == 1


def test_the_pane_is_in_tabs_and_remembers_which(view):
    titles = [view.tabs.tabText(i) for i in range(view.tabs.count())]
    assert titles == ["DIDs, routines and raw", "DTCs", "ECU control", "Transfer"]
    view.tabs.setCurrentIndex(3)
    again = UdsView(view.manager, view.ctx)
    assert again.tabs.currentIndex() == 3


def test_the_log_has_the_space_the_controls_do_not_need(app, view):
    """The scroll area stretched and left a gap under the tabs; the log should."""
    from PySide6.QtWidgets import QScrollArea

    view.resize(900, 1600)
    view.show()
    app.processEvents()
    scroll = view.findChild(QScrollArea)
    assert scroll.height() <= scroll.widget().sizeHint().height() + 4
    assert view.output.height() > 400
    open_right = view.open_btn.geometry().right()
    assert open_right > view.open_btn.parentWidget().width() - 40, "Open is at the right"
    view.hide()


def test_dids_and_routines_have_a_box_each_and_a_readable_list(view):
    """In one grid they shared columns, and the list was too narrow to read."""
    boxes = (view.did.parentWidget(), view.routine.parentWidget())
    assert all(isinstance(box, QGroupBox) for box in boxes) and boxes[0] is not boxes[1]
    for picker in (view.did, view.routine, view.report):
        listing = picker.view()
        assert listing.minimumWidth() >= listing.sizeHintForColumn(0), "entries are not cut off"


# --- Read all DTCs --------------------------------------------------------------------------
class ScriptedEcu(Answering):
    """Answers the requests it has a reply for, and NRC 0x12 to everything else."""

    def __init__(self, replies: dict[str, str]) -> None:
        super().__init__()
        self.replies = {bytes.fromhex(k): bytes.fromhex(v) for k, v in replies.items()}

    def specific_send(self, payload: bytes) -> None:
        self.sent.append(bytes(payload))
        self._reply = self.replies.get(bytes(payload), bytes([0x7F, payload[0], 0x12]))


FAULTY_ECU = {
    "19 01 FF": "59 01 FF 01 00 02",  # two DTCs match
    "19 02 FF": "59 02 FF 012345 09 C01001 2F",
    # Extended data: record 01 one byte, record 02 two bytes.
    "19 06 012345 FF": "59 06 012345 09 01 03 02 0064",
    "19 06 C01001 FF": "59 06 C01001 2F 01 01 02 000A",
    "19 03": "59 03 012345 01",  # one snapshot, record 01 of the first
    # The snapshot: record 01, two DIDs -- F190 three bytes, 0102 one byte.
    "19 04 012345 01": "59 04 012345 09 01 02 F190 414243 0102 05",
    "22 F1 90": "62 F1 90 414243",
    "22 01 02": "62 01 02 05",
    "19 0B": "59 0B FF 012345 09",
    "19 14": "59 14 012345 05",
    "19 15": "59 15 FF",
}


@pytest.fixture
def faulty(manager, monkeypatch):
    from udsoncan.client import Client

    ecu = ScriptedEcu(FAULTY_ECU)
    manager.client = Client(ecu)
    manager.client.open()
    real = manager._hooks.call

    def call(module, name, *args, **kwargs):
        if name == "extended_data_record":
            return {1: (1, "Occurrence counter"), 2: (2, "Operating hours")}.get(args[0])
        return real(module, name, *args, **kwargs)

    monkeypatch.setattr(manager._hooks, "call", call)
    lines: list[str] = []
    manager.result.connect(lines.append)
    return ecu, lines


def test_read_all_gives_every_fault_its_records_and_snapshots(manager, faulty):
    _ecu, lines = faulty
    manager.read_all_dtcs(0xFF)
    report = "\n".join(lines)

    assert "2 DTCs match status mask 0xFF" in report
    assert "01 Occurrence counter = 03" in report and "02 Operating hours = 00 64" in report
    assert "02 Operating hours = 00 0A" in report, "the second DTC's records too"
    assert "1 snapshot stored" in report
    assert 'F190 (VIN) = 41 42 43  "ABC"' in report, "a snapshot's DIDs named and decoded"
    assert "0102" in report and "= 05" in report
    assert lines[-1] == "End of DTC data"


def test_a_report_the_ecu_does_not_offer_is_one_line_and_the_rest_goes_on(manager, faulty):
    ecu, lines = faulty
    manager.read_all_dtcs(0xFF)
    report = "\n".join(lines)
    asked_severity = [r for r in ecu.sent if r[:2] == bytes([0x19, 0x09])]
    assert len(asked_severity) == 1, "not asked again for the second DTC"
    assert "First confirmed DTC: not supported by this ECU (NRC 0x12)" in report
    assert "Fault detection counters" in report, "and the reports after it still come"
    assert not any(r[:2] == bytes([0x19, 0x0A]) for r in ecu.sent), "supported DTCs only if asked"


def test_the_single_extended_data_report_works_without_udsoncan_s_sizes(manager, faulty):
    """udsoncan refuses report 0x06 unless told every record's size first."""
    _ecu, lines = faulty
    manager.read_dtc_information(0x06, dtc=0x012345, extended_data_record_number=0xFF)
    assert "01 Occurrence counter = 03" in lines[-1]


def test_records_of_unknown_size_are_shown_rather_than_guessed():
    from pycangui.uds.manager import split_extended, split_snapshots

    assert split_extended(bytes.fromhex("01 03 02 0064"), lambda n: None) == [
        "01 onwards, not split = 01 03 02 00 64 (sizes from EXTENDED_DATA_RECORDS in hooks/uds.py)"
    ]
    assert split_extended(bytes.fromhex("07 AABB"), lambda n: None, single=True) == ["07 = AA BB"]
    lines = split_snapshots(bytes.fromhex("01 01 F190 414243"), lambda d: None, None)
    assert lines == ["record 01", '  F190 onwards, not split = 41 42 43  "ABC"']


def test_a_snapshot_did_the_ecu_will_not_read_is_split_by_its_size_from_the_hook(
    manager, monkeypatch
):
    """Reading the DID is how its length is learned, and an ECU need not allow
    that in the session a snapshot is read in."""
    from udsoncan.client import Client

    replies = {k: v for k, v in FAULTY_ECU.items() if k != "22 01 02"}  # refused
    ecu = ScriptedEcu(replies)
    manager.client = Client(ecu)
    manager.client.open()
    real = manager._hooks.call
    monkeypatch.setattr(
        manager._hooks,
        "call",
        lambda module, name, *a, **k: (
            {0x0102: 1}.get(a[0]) if name == "did_size" else real(module, name, *a, **k)
        ),
    )
    lines = []
    manager.result.connect(lines.append)

    manager.read_dtc_information(0x04, dtc=0x012345, snapshot_record_number=0x01)

    assert "0102 = 05" in lines[-1], "split, named and decoded"
    assert "not split" not in lines[-1]
    assert bytes.fromhex("22 01 02") not in ecu.sent, "the hook answered, so it was not read"


def test_a_dtc_with_no_status_bits_set_has_no_gap_before_its_description(manager):
    """Seen on a real ECU's permanent DTC list: "status 0x00  - Control ..."."""
    from udsoncan import Dtc

    d = Dtc(0x060742)
    d.status.set_byte(0x00)
    manager._hooks.call = lambda *a, **k: "Control Module Performance"
    line = manager._describe_dtc(d)[0]
    assert "status 0x00 - Control Module Performance" in line


# --- the sequence round a download ------------------------------------------------------
def test_the_steps_are_ticked_in_two_menus_in_the_order_they_are_done(view):
    from pycangui.uds import sequence

    before = [a.text() for a in view.before_btn.menu().actions()]
    after = [a.text() for a in view.after_btn.menu().actions()]
    assert before == [s.title for s in sequence.STEPS if s.phase == sequence.BEFORE]
    assert after == [s.title for s in sequence.STEPS if s.phase == sequence.AFTER]
    assert view.steps_chosen() == set(), "a download is a download until something is ticked"


def test_erase_and_check_are_the_same_ticks_as_before(view):
    view.step_actions["erase"].setChecked(True)
    view.step_actions["check"].setChecked(True)
    assert view.erase.isChecked() and view.check.isChecked()
    view.erase.setChecked(False)
    assert not view.step_actions["erase"].isChecked()


def test_the_steps_ticked_are_remembered(view):
    view.step_actions["programming"].setChecked(True)
    view.step_actions["reset"].setChecked(True)
    again = UdsView(view.manager, view.ctx)
    assert again.steps_chosen() == {"programming", "reset"}


def test_a_download_with_steps_ticked_is_run_as_a_sequence(view, images_dir, monkeypatch):
    monkeypatch.setattr(QMessageBox, "exec", lambda _box: QMessageBox.Yes)
    view.manager.client = ecu = FakeEcu()
    view.local.setText(images_dir("a.hex", (0x8000, bytes(8))))
    view._reload_image()
    ran = []
    monkeypatch.setattr(view.sequence, "run", lambda chosen, values, transfer: ran.append(chosen))

    view.start.click()
    assert ran == [] and any(c[0] == "download" for c in ecu.calls), (
        "none ticked: the download alone"
    )

    view.step_actions["erase"].setChecked(True)
    view.start.click()
    assert ran == [], "erase is part of the download, and no sequence of its own"

    view.step_actions["programming"].setChecked(True)
    view.start.click()
    assert ran == [{"erase", "programming"}]


def test_the_steps_are_only_offered_for_a_download(view):
    assert view.before_btn.isEnabled() and view.after_btn.isEnabled()
    view.operation.setCurrentIndex(1)
    assert not view.before_btn.isEnabled() and not view.values_btn.isEnabled()


def test_cancel_during_a_sequence_stops_the_sequence(view, monkeypatch):
    stopped = []
    monkeypatch.setattr(view.sequence, "cancel", lambda: stopped.append("sequence"))
    monkeypatch.setattr(view.manager, "cancel_transfer", lambda: stopped.append("transfer"))
    view.stop.setEnabled(True)
    view.stop.click()
    view.sequence.is_running = True
    view.stop.click()
    view.sequence.is_running = False
    assert stopped == ["transfer", "sequence"]


def test_the_sequence_values_are_asked_for_and_kept(app, view):
    from pycangui.ui import uds_sequence

    dialog = uds_sequence.ValuesDialog(None, uds_sequence.load(view.ctx.settings))
    dialog.unlock_programming.setValue(3)
    dialog.check_routine.setText("0301")
    dialog.fingerprint_data.setText("zz")
    assert dialog.problem(), "bytes that are not hex are not taken"
    dialog.fingerprint_data.setText("20 26 10 10")
    assert not dialog.problem()
    uds_sequence.save(view.ctx.settings, dialog.values())
    again = uds_sequence.load(view.ctx.settings)
    assert again.unlock_programming == 3 and again.fingerprint_data == "20 26 10 10"
    assert again.check_routine == 0x0301


def test_a_check_routine_typed_in_the_old_box_is_still_the_one_used(view):
    from pycangui.ui import uds_sequence

    view.ctx.settings.set("uds.transfer.check_routine", "0455")
    assert uds_sequence.load(view.ctx.settings).check_routine == 0x0455
