# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware download over CANopen, and the plugin API carrying a real screen.

The sequence is CiA 302-3, which is the only written-down way of doing this --
and the way most devices do not do it.  What is tested here is that the steps
are the ones the standard says, in the order it says, and that the parts which
cannot be honest about a device refuse rather than guess.

Nothing here needs a bus: the sequence is handed something that can read and
write objects, and a dictionary does that as well as a controller.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.core import plugin_package
from pycangui.core.plugins import builtin_dir
from pycangui.plugins.firmware import program
from pycangui.plugins.firmware.program import CLEAR, PROGRAM_CONTROL, PROGRAM_DATA, START, STOP
from pycangui.ui.main_window import MainWindow


class FakeDevice(program.Device):
    """Somewhere to write, and a record of what was written."""

    def __init__(self, values=None):
        self.written: list[tuple] = []
        self.domains: list[tuple] = []
        self.values = values or {}

    def write(self, index, sub, value):
        self.written.append((index, sub, value))

    def read(self, index, sub):
        if (index, sub) not in self.values:
            raise KeyError("this device does not keep one")
        return self.values[(index, sub)]

    def write_domain(self, index, sub, data, progress):
        self.domains.append((index, sub, data))
        progress(len(data), len(data))


class Segment:
    def __init__(self, address, data):
        self.address, self.data = address, data


class Image:
    def __init__(self, segments, path="firmware.hex"):
        self.segments, self.path = segments, path


def run(device, program_number=1, data=b"firmware"):
    said = []
    for step in program.steps(device, program_number, data, lambda _d, _t: None):
        said.append(step.what)
        step.run()
    return said


# --- the sequence -------------------------------------------------------------------------
def test_it_stops_clears_writes_and_starts_in_that_order():
    """CiA 302-3: a program is stopped and cleared before it is replaced, and
    a device asked to run one it has half of is a device nobody can talk to."""
    device = FakeDevice()
    run(device)

    assert device.written == [
        (PROGRAM_CONTROL, 1, STOP),
        (PROGRAM_CONTROL, 1, CLEAR),
        (PROGRAM_CONTROL, 1, START),
    ]
    assert device.domains == [(PROGRAM_DATA, 1, b"firmware")]


def test_each_step_says_what_it_is_doing_before_it_does_it():
    """A download that fails half way should say which half."""
    said = run(FakeDevice())
    assert said[0].startswith("Stopping")
    assert "8 bytes" in said[2]
    assert said[-1].startswith("Starting")


def test_a_second_processor_is_a_second_program():
    """The case the manual warns about: a controller taking two images."""
    device = FakeDevice()
    run(device, program_number=2)
    assert {sub for _index, sub, _value in device.written} == {2}
    assert device.domains[0][1] == 2


def test_stopping_the_program_is_what_puts_it_in_its_loader():
    device = FakeDevice()
    program.enter_bootloader(device, 1)
    assert device.written == [(PROGRAM_CONTROL, 1, STOP)]


def test_leaving_the_loader_is_starting_the_program_again():
    device = FakeDevice()
    program.exit_bootloader(device, 1)
    assert device.written == [(PROGRAM_CONTROL, 1, START)]


def test_the_software_version_is_read_as_the_string_it_is():
    device = FakeDevice({(program.SOFTWARE_VERSION, 0): b"V2.1.0\x00\x00"})
    assert program.software_version(device) == "V2.1.0", "the padding taken off"
    device = FakeDevice({(program.SOFTWARE_VERSION, 0): "V2.1.0"})
    assert program.software_version(device) == "V2.1.0"
    assert program.software_version(FakeDevice()) == ""


def test_progress_is_reported_while_the_bytes_go():
    seen = []
    for step in program.steps(FakeDevice(), 1, b"x" * 4096, lambda d, t: seen.append((d, t))):
        step.run()
    assert seen == [(4096, 4096)]


# --- what it will not do ----------------------------------------------------------------------
def test_an_image_with_holes_in_it_is_refused():
    """A program download is one block of bytes.  Filling the gaps would put
    invented bytes into somebody's flash."""
    image = Image([Segment(0, b"aaaa"), Segment(0x1000, b"bbbb")])
    with pytest.raises(ValueError, match="pieces"):
        program.image_bytes(image)


def test_an_empty_image_is_refused():
    with pytest.raises(ValueError, match="nothing in it"):
        program.image_bytes(Image([]))


def test_one_contiguous_image_is_the_bytes():
    assert program.image_bytes(Image([Segment(0x8000, b"firmware")])) == b"firmware"


def test_what_a_device_does_not_keep_is_reported_as_not_kept():
    """Rather than as an error: most devices keep neither of these."""
    assert program.identification(FakeDevice(), 1) == ""
    assert program.flash_status(FakeDevice(), 1) == ""


def test_what_it_does_keep_is_shown_as_the_number_it_is():
    device = FakeDevice({(program.PROGRAM_IDENTIFICATION, 1): 0xDEADBEEF})
    assert program.identification(device, 1) == "0xDEADBEEF"


# --- a live node: segmented or block ------------------------------------------------------------
class Variable:
    def __init__(self, raw):
        self.raw = raw


class Sink:
    """Where an opened domain goes, kept after it is closed."""

    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, chunk):
        self.data += chunk
        return len(chunk)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True


class FakeSdo:
    def __init__(self, objects=None, uploads=None, refuse_block=False):
        self.objects = objects or {}
        self.uploads = uploads or {}
        self.refuse_block = refuse_block
        self.opened: list[tuple] = []
        self.sink = Sink()

    def __getitem__(self, index):
        return self.objects[index]  # KeyError where the EDS does not have it

    def upload(self, index, sub):
        return self.uploads[(index, sub)]

    def open(self, index, sub, mode, size=None, block_transfer=False):
        if block_transfer and self.refuse_block:
            raise RuntimeError("Code 0x05040001, Client/server command specifier not valid")
        self.opened.append((index, sub, mode, size, block_transfer))
        return self.sink


class FakeNode:
    def __init__(self, sdo):
        self.sdo = sdo


def test_segmented_unless_block_is_chosen():
    from pycangui.plugins.firmware.plugin import NodeDevice

    sdo = FakeSdo()
    NodeDevice(FakeNode(sdo)).write_domain(PROGRAM_DATA, 1, b"x" * 10, lambda _d, _t: None)
    assert sdo.opened == [(PROGRAM_DATA, 1, "wb", 10, False)]


def test_block_transfer_is_asked_for_when_chosen():
    from pycangui.plugins.firmware.plugin import NodeDevice

    sdo = FakeSdo()
    seen = []
    data = bytes(range(256)) * 12  # 3072 bytes: three writes
    NodeDevice(FakeNode(sdo), block=True).write_domain(
        PROGRAM_DATA, 1, data, lambda d, t: seen.append((d, t))
    )
    assert sdo.opened == [(PROGRAM_DATA, 1, "wb", len(data), True)]
    assert sdo.sink.data == data and sdo.sink.closed
    assert seen[-1] == (len(data), len(data))


def test_a_device_that_refuses_block_transfer_says_what_to_do():
    from pycangui.plugins.firmware.plugin import NodeDevice

    device = NodeDevice(FakeNode(FakeSdo(refuse_block=True)), block=True)
    with pytest.raises(RuntimeError, match="try Segmented"):
        device.write_domain(PROGRAM_DATA, 1, b"firmware", lambda _d, _t: None)


def test_reading_goes_through_the_eds_where_it_describes_the_object():
    from pycangui.plugins.firmware.plugin import NodeDevice

    sdo = FakeSdo({0x100A: Variable("V3.0"), 0x1F56: {1: Variable(0x1234)}})
    device = NodeDevice(FakeNode(sdo))
    assert device.read(0x100A, 0) == "V3.0", "a plain variable has no sub-indices"
    assert device.read(0x1F56, 1) == 0x1234


def test_a_node_with_no_eds_is_still_asked_what_it_runs():
    """A device in its loader is the usual node nobody loaded an EDS for."""
    from pycangui.plugins.firmware.plugin import NodeDevice

    device = NodeDevice(FakeNode(FakeSdo(uploads={(0x100A, 0): b"BOOT 1.4\x00"})))
    assert program.software_version(device) == "BOOT 1.4"


# --- and as a plugin ----------------------------------------------------------------------------
def install(window) -> None:
    """As the menu does it, without the question in front of it."""
    plugin_package.install_folder(builtin_dir() / "firmware", window.ctx.workspace_dir / "plugins")
    window._reload_plugins()


@pytest.fixture
def bare(app, tmp_path, monkeypatch):
    """A window as it comes: nothing installed."""
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


@pytest.fixture
def window(app, bare):
    install(bare)
    app.processEvents()
    return bare


def test_it_is_supplied_rather_than_present(app, bare):
    """Nothing pycangui ships is loaded until somebody installs it.  A screen
    nobody asked for in every window is what installing is there to prevent."""
    assert "firmware:main" not in bare.panes.docks
    assert bare.plugins.loaded == {}


def test_it_installs_and_loads(app, window):
    """The first thing built through the plugin API, which was the point of
    building it that way: an API with no real screen behind it is a guess."""
    assert "CANopen firmware (CiA 302-3)" in [r.label for r in window.plugins.working()]
    assert window.plugins.errors() == {}


def test_it_says_which_version_it_is(app, window):
    """A plugin travels, and the copy in a workspace is the user's own: the
    only way to know which of ours it started as is for it to say."""
    assert window.plugins.loaded["firmware"].version == "1.1"


def test_it_brings_a_pane(app, window):
    assert "firmware:main" in window.panes.docks
    assert window.panes.docks["firmware:main"].windowTitle() == "CANopen firmware"
    assert "CANopen firmware" in [a.text() for a in window.view_menu.actions()]


def test_a_users_own_replaces_it(app, window):
    """Which is the whole reason it is a plugin: most devices want a sequence
    of their maker's own, and installing puts the copy they edit in front of
    them rather than leaving them to find ours."""
    folder = window.ctx.workspace_dir / "plugins" / "firmware"
    (folder / "plugin.py").write_text(
        "NAME = 'Firmware (ours)'\ndef register(app):\n    pass\n", encoding="utf-8"
    )
    window._reload_plugins()
    app.processEvents()

    assert [r.label for r in window.plugins.working()] == ["Firmware (ours)"]
    assert "firmware:main" not in window.panes.docks, "and its pane went with it"


def test_programming_with_no_node_says_so_rather_than_failing(app, window):
    view = window.panes.view("firmware:main")
    view._download()
    assert "No node selected" in window.log.toPlainText()


def test_the_pane_offers_the_loader_the_version_and_the_transfer(app, window):
    view = window.panes.view("firmware:main")
    assert view.enter.text() == "Enter bootloader"
    assert view.leave.text() == "Exit bootloader"
    assert view.version.text() == "Read version"
    offered = [view.transfer.itemText(i) for i in range(view.transfer.count())]
    assert offered == ["Segmented", "Block"], "segmented first: every device takes it"


def test_the_transfer_chosen_is_the_one_used_and_kept(app, window, monkeypatch):
    view = window.panes.view("firmware:main")
    view.node.addItem("Node 5", 5)
    view.node.setCurrentIndex(view.node.count() - 1)
    monkeypatch.setattr(window.canopen, "node", lambda _node_id: FakeNode(FakeSdo()))

    assert view._device().block is False
    view.transfer.setCurrentText("Block")
    assert view._device().block is True
    assert window.ctx.settings.get("plugins.firmware.transfer") == "Block"


def refusing(window, monkeypatch):
    """Answer No to every question, and note what was asked."""
    asked = []
    monkeypatch.setattr(
        window.confirm, "ask", lambda _p, key, title, text: asked.append((key, text)) or False
    )
    return asked


def on_node_5(window, view, monkeypatch):
    sdo = FakeSdo()
    view.node.addItem("Node 5", 5)
    view.node.setCurrentIndex(view.node.count() - 1)
    monkeypatch.setattr(window.canopen, "node", lambda _node_id: FakeNode(sdo))
    return sdo


def test_entering_the_bootloader_asks_first(app, window, monkeypatch):
    """Stopping the program stops whatever it was controlling, which is the
    same class of thing as enabling a drive."""
    view = window.panes.view("firmware:main")
    on_node_5(window, view, monkeypatch)
    asked = refusing(window, monkeypatch)

    view.enter.click()
    assert asked and asked[0][0] == "firmware.stop.5", "and once per node, not once ever"
    assert "stops being controlled" in asked[0][1]
    assert not view._busy and view.state.text() == "", "No means nothing was started"


def test_a_download_asks_first_too(app, window, monkeypatch):
    """Its first step stops the program."""
    view = window.panes.view("firmware:main")
    on_node_5(window, view, monkeypatch)
    asked = refusing(window, monkeypatch)
    view.image = Image([Segment(0, b"firmware")])

    view._download()
    assert [key for key, _text in asked] == ["firmware.stop.5"]
    assert not view._busy and not view.progress.isVisible()


def test_leaving_the_loader_and_reading_the_version_do_not_ask(app, window, monkeypatch):
    view = window.panes.view("firmware:main")
    on_node_5(window, view, monkeypatch)
    asked = refusing(window, monkeypatch)
    ran = []
    monkeypatch.setattr(view.app, "run_in_background", lambda job, done: ran.append(job))

    view.leave.click()
    view._busy = False
    view.version.click()
    assert asked == []
    assert len(ran) == 2, "both went ahead"


def test_the_bootloader_with_no_node_says_so_rather_than_failing(app, window):
    view = window.panes.view("firmware:main")
    view.enter.click()
    view.version.click()
    assert window.log.toPlainText().count("No node selected") == 2


def test_an_image_that_will_not_read_is_reported(app, window, tmp_path):
    view = window.panes.view("firmware:main")
    bad = tmp_path / "not-an-image.hex"
    bad.write_text("this is not Intel HEX\n", encoding="utf-8")
    view.path.setText(str(bad))
    view._reload_image()
    assert view.image is None
    assert view.summary.text()
