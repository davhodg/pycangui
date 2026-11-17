# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Firmware download over CANopen: the CANopen pane's Firmware tab, and its hooks.

The default sequence is CiA 302-3, which is the only written-down way of doing
this -- and the way most devices do not do it. What is tested here is that the
steps are the ones the standard says, in the order it says; that the parts which
cannot be honest about a device refuse rather than guess; and that each part
can be replaced from hooks/canopen.py, chosen by the node's identity.

Nothing here needs a bus: the sequence is handed something that can read and
write objects, and a dictionary does that as well as a controller.
"""

import pytest
from PySide6.QtCore import QSettings

from pycangui.canopen import NodeIdentity
from pycangui.canopen import firmware as program
from pycangui.canopen.firmware import (
    CLEAR,
    PROGRAM_CONTROL,
    PROGRAM_DATA,
    START,
    STOP,
    Firmware,
    NodeDevice,
)
from pycangui.core import workspaces
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


def test_each_step_is_a_step_of_its_own():
    """A download that fails half way should say which half."""
    assert len(run(FakeDevice())) == 4


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
    """A program download is one block of bytes. Filling the gaps would put
    invented bytes into somebody's flash."""
    image = Image([Segment(0, b"aaaa"), Segment(0x1000, b"bbbb")])
    with pytest.raises(ValueError):
        program.image_bytes(image)


def test_an_empty_image_is_refused():
    with pytest.raises(ValueError):
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
    RESPONSE_TIMEOUT = 0.3  # the canopen package's own

    def __init__(self, objects=None, uploads=None, refuse_block=False):
        self.objects = objects or {}
        self.uploads = uploads or {}
        self.refuse_block = refuse_block
        self.opened: list[tuple] = []
        #: The timeout in force each time a domain was opened.
        self.timeouts: list[float] = []
        self.sink = Sink()

    def __getitem__(self, index):
        return self.objects[index]  # KeyError where the EDS does not have it

    def upload(self, index, sub):
        return self.uploads[(index, sub)]

    def open(self, index, sub, mode, size=None, block_transfer=False):
        if block_transfer and self.refuse_block:
            raise RuntimeError("Code 0x05040001, Client/server command specifier not valid")
        self.opened.append((index, sub, mode, size, block_transfer))
        self.timeouts.append(self.RESPONSE_TIMEOUT)
        return self.sink


class FakeNode:
    def __init__(self, sdo):
        self.sdo = sdo


def test_segmented_unless_block_is_chosen():
    sdo = FakeSdo()
    NodeDevice(FakeNode(sdo)).write_domain(PROGRAM_DATA, 1, b"x" * 10, lambda _d, _t: None)
    assert sdo.opened == [(PROGRAM_DATA, 1, "wb", 10, False)]


def test_block_transfer_is_asked_for_when_chosen():
    sdo = FakeSdo()
    seen = []
    data = bytes(range(256)) * 12  # 3072 bytes: three writes
    NodeDevice(FakeNode(sdo), block=True).write_domain(
        PROGRAM_DATA, 1, data, lambda d, t: seen.append((d, t))
    )
    assert sdo.opened == [(PROGRAM_DATA, 1, "wb", len(data), True)]
    assert sdo.sink.data == data and sdo.sink.closed
    assert seen[-1] == (len(data), len(data))


def test_a_device_that_refuses_block_transfer_is_still_an_error():
    device = NodeDevice(FakeNode(FakeSdo(refuse_block=True)), block=True)
    with pytest.raises(RuntimeError):
        device.write_domain(PROGRAM_DATA, 1, b"firmware", lambda _d, _t: None)


def test_reading_goes_through_the_eds_where_it_describes_the_object():
    sdo = FakeSdo({0x100A: Variable("V3.0"), 0x1F56: {1: Variable(0x1234)}})
    device = NodeDevice(FakeNode(sdo))
    assert device.read(0x100A, 0) == "V3.0", "a plain variable has no sub-indices"
    assert device.read(0x1F56, 1) == 0x1234


def test_a_node_with_no_eds_is_still_asked_what_it_runs():
    """A device in its loader is the usual node nobody loaded an EDS for."""
    device = NodeDevice(FakeNode(FakeSdo(uploads={(0x100A, 0): b"BOOT 1.4\x00"})))
    assert program.software_version(device) == "BOOT 1.4"


# --- a longer timeout while it erases and programs ---------------------------------------------
def test_a_node_is_given_longer_while_it_programs_and_then_put_back():
    sdo = FakeSdo()
    with NodeDevice(FakeNode(sdo)).patient(10):
        assert sdo.RESPONSE_TIMEOUT == 10
    assert sdo.RESPONSE_TIMEOUT == 0.3


def test_it_is_put_back_when_programming_fails_too():
    sdo = FakeSdo()
    with pytest.raises(RuntimeError), NodeDevice(FakeNode(sdo)).patient(10):
        raise RuntimeError("No SDO response received")
    assert sdo.RESPONSE_TIMEOUT == 0.3


def test_a_timeout_already_longer_is_not_shortened():
    sdo = FakeSdo()
    sdo.RESPONSE_TIMEOUT = 30
    with NodeDevice(FakeNode(sdo)).patient(10):
        assert sdo.RESPONSE_TIMEOUT == 30


# --- the hooks ---------------------------------------------------------------------------------
MOTOR = NodeIdentity(5, vendor_id=0x123, product_code=0x1234, revision=1)
OTHER = NodeIdentity(6, vendor_id=0x123, product_code=0x9999, revision=1)

#: A maker's own sequence, for one product only: anything else gets CiA 302-3.
OWN_HOOKS = """
from pycangui.canopen.firmware import Step
from pycangui.core.hooks import hook


def _ours(identity):
    return identity is not None and identity.product_code == 0x1234


@hook
def firmware_check(device, identity, image, program, *, ctx):
    if _ours(identity) and "motor" not in image.path:
        return "not the motor firmware"
    return None


@hook
def enter_bootloader(device, identity, program, *, ctx):
    if _ours(identity):
        device.write(0x2F10, 0, 0xB007)
        return True
    return None


@hook
def exit_bootloader(device, identity, program, *, ctx):
    if _ours(identity):
        device.write(0x2F10, 0, 0)
        return True
    return None


@hook
def firmware_steps(device, identity, image, program, progress, *, ctx):
    if not _ours(identity):
        return None
    return [
        Step("Unlocking", lambda: device.write(0x2F10, 1, 0x55AA)),
        *[
            Step(f"Writing piece {n}",
                 lambda s=s: device.write_domain(0x2F11, 0, s.data, progress))
            for n, s in enumerate(image.segments, 1)
        ],
    ]


@hook
def firmware_version(device, identity, program, *, ctx):
    return "motor 7.1" if _ours(identity) else None


@hook
def flash_status(device, identity, program, *, ctx):
    return "good" if _ours(identity) else None
"""


@pytest.fixture
def hooks(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    made = Hooks(Context(log=print))
    (workspaces.hooks_dir() / "canopen.py").write_text(OWN_HOOKS, encoding="utf-8")
    made.reload()
    return made


def test_the_hooks_template_has_the_firmware_hooks_and_they_default_to_the_book(app):
    from pycangui.core.hooks import registry

    names = set(registry()["canopen"])
    assert {
        "firmware_check",
        "enter_bootloader",
        "exit_bootloader",
        "firmware_steps",
        "firmware_version",
        "flash_status",
    } <= names
    device = FakeDevice()
    Firmware(None, MOTOR).enter(device, 1)
    assert device.written == [(PROGRAM_CONTROL, 1, STOP)]


def test_a_product_with_a_sequence_of_its_own_gets_it(hooks):
    device = FakeDevice()
    firmware = Firmware(hooks, MOTOR)
    image = Image([Segment(0, b"aaaa"), Segment(0x1000, b"bbbb")], path="motor.hex")
    assert firmware.check(device, image, 1) is None
    for step in firmware.steps(device, image, 1, lambda _d, _t: None):
        step.run()
    assert device.written == [(0x2F10, 1, 0x55AA)]
    assert device.domains == [(0x2F11, 0, b"aaaa"), (0x2F11, 0, b"bbbb")], "pieces and all"


def test_another_product_gets_the_book(hooks):
    device = FakeDevice()
    firmware = Firmware(hooks, OTHER)
    for step in firmware.steps(device, Image([Segment(0, b"fw")]), 1, lambda _d, _t: None):
        step.run()
    assert device.written == [
        (PROGRAM_CONTROL, 1, STOP),
        (PROGRAM_CONTROL, 1, CLEAR),
        (PROGRAM_CONTROL, 1, START),
    ]


def test_the_check_can_refuse(hooks):
    image = Image([Segment(0, b"fw")], path="brake.hex")
    assert Firmware(hooks, MOTOR).check(FakeDevice(), image, 1)
    assert Firmware(hooks, OTHER).check(FakeDevice(), image, 1) is None


def test_the_loader_and_the_version_follow_the_hooks(hooks):
    device = FakeDevice()
    firmware = Firmware(hooks, MOTOR)
    firmware.enter(device, 1)
    firmware.leave(device, 1)
    assert device.written == [(0x2F10, 0, 0xB007), (0x2F10, 0, 0)]
    assert firmware.version(device, 1) == "motor 7.1"
    assert firmware.status(device, 1) == ("", "good")
    other = FakeDevice({(program.SOFTWARE_VERSION, 0): "V1"})
    assert Firmware(hooks, OTHER).version(other, 1) == "V1"


def test_a_node_that_has_not_said_who_it_is_gets_the_book(hooks):
    device = FakeDevice()
    Firmware(hooks, None).enter(device, 1)
    assert device.written == [(PROGRAM_CONTROL, 1, STOP)]


# --- the tab in the CANopen pane ----------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    yield win
    win.close()


@pytest.fixture
def tab(window):
    return window.canopen_view.firmware


def on_node_5(window, tab, monkeypatch, sdo=None):
    sdo = sdo or FakeSdo()
    monkeypatch.setattr(tab, "selected_node", lambda: 5)
    monkeypatch.setattr(window.canopen, "node", lambda _node_id: FakeNode(sdo))
    tab.node_changed()
    return sdo


def refusing(window, monkeypatch):
    """Answer No to every question, and note what was asked."""
    asked = []
    monkeypatch.setattr(
        window.confirm, "ask", lambda _p, key, title, text: asked.append(key) or False
    )
    return asked


def in_line(window, monkeypatch):
    """Run the CANopen worker's jobs at once, on this thread, noting each."""
    ran = []

    def run(job, done=None):
        ran.append(job)
        result, error = None, None
        try:
            result = job()
        except Exception as exc:
            error = str(exc)
        if done is not None:
            done(result, error)

    monkeypatch.setattr(window.canopen, "background", run)
    return ran


def test_it_is_a_tab_of_the_canopen_pane_and_no_longer_a_plugin(window, tab):
    tabs = window.canopen_view.bottom_tabs
    assert tabs.indexOf(tab) >= 0
    from pycangui.core.plugins import supplied

    assert "firmware" not in [entry.name for entry in supplied()]


def test_with_no_node_selected_nothing_can_be_started(window, tab, monkeypatch):
    monkeypatch.setattr(tab, "selected_node", lambda: None)
    tab.node_changed()
    assert not tab.download.isEnabled() and not tab.enter.isEnabled()
    ran = in_line(window, monkeypatch)
    tab._download()
    tab.enter_bootloader()
    tab.read_version()
    assert ran == [] and not tab._busy


def test_segmented_is_first_and_the_choice_is_kept(window, tab):
    offered = [tab.transfer.itemText(i) for i in range(tab.transfer.count())]
    assert offered == ["Segmented", "Block"], "segmented first: every device takes it"
    tab.transfer.setCurrentText("Block")
    assert window.ctx.settings.get("canopen.firmware.transfer") == "Block"


def test_entering_the_bootloader_asks_first(window, tab, monkeypatch):
    """Stopping the program stops whatever it was controlling, which is the
    same class of thing as enabling a drive."""
    sdo = on_node_5(window, tab, monkeypatch)
    asked = refusing(window, monkeypatch)
    ran = in_line(window, monkeypatch)

    tab.enter.click()
    assert asked == ["firmware.stop.5"], "and once per node, not once ever"
    assert ran == [] and sdo.opened == []


def test_a_download_asks_first_too(window, tab, monkeypatch):
    on_node_5(window, tab, monkeypatch)
    asked = refusing(window, monkeypatch)
    ran = in_line(window, monkeypatch)
    tab.image = Image([Segment(0, b"firmware")])

    tab._download()
    assert asked == ["firmware.stop.5"]
    assert ran == [] and not tab.progress.isVisible()


def test_an_image_in_pieces_is_refused_with_nothing_written(window, tab, monkeypatch):
    """CiA 302-3 is one block of bytes; the sequence is worked out before the
    first step, so the node is not even stopped."""
    sdo = on_node_5(window, tab, monkeypatch)
    written = []

    class Control:
        raw = property(lambda self: 0, lambda self, value: written.append(value))

    sdo.objects[PROGRAM_CONTROL] = {1: Control()}
    monkeypatch.setattr(window.confirm, "ask", lambda *_a: True)
    in_line(window, monkeypatch)
    tab.image = Image([Segment(0, b"aaaa"), Segment(0x1000, b"bbbb")])
    tab._download()
    assert written == [] and sdo.opened == []
    assert not tab._busy and not window.ctx.pause.paused


def test_leaving_the_loader_and_reading_the_version_do_not_ask(window, tab, monkeypatch):
    on_node_5(window, tab, monkeypatch)
    asked = refusing(window, monkeypatch)
    ran = in_line(window, monkeypatch)
    tab.leave.click()
    tab.version.click()
    assert asked == []
    assert len(ran) == 2, "both went ahead"


def test_the_whole_download_runs_with_the_programming_timeout(window, tab, monkeypatch):
    """The erase is the slow one, but the last block and the start that checks
    the image can be as slow, so the whole sequence gets it."""
    sdo = on_node_5(window, tab, monkeypatch)
    seen = []

    class Control:
        raw = property(
            lambda self: 0, lambda self, value: seen.append((value, sdo.RESPONSE_TIMEOUT))
        )

    sdo.objects[PROGRAM_CONTROL] = {1: Control()}
    monkeypatch.setattr(window.confirm, "ask", lambda *_a: True)
    in_line(window, monkeypatch)
    tab.patience.setValue(20)
    tab.image = Image([Segment(0, b"firmware")])

    tab._download()
    assert seen == [(STOP, 20), (CLEAR, 20), (START, 20)]
    assert sdo.timeouts == [20]
    assert sdo.sink.data == b"firmware"
    assert sdo.RESPONSE_TIMEOUT == 0.3, "and the usual one afterwards"
    assert window.ctx.settings.get("canopen.firmware.programming_timeout_s") == 20
    assert not tab._busy and tab.download.isEnabled()


def test_programming_holds_the_rest_of_pycangui_back_while_it_runs(window, tab, monkeypatch):
    """A node rebooting into its loader was identified, and its PDOs read,
    over SDO in the middle of the download."""
    sdo = on_node_5(window, tab, monkeypatch)
    sdo.objects[PROGRAM_CONTROL] = {1: type("Control", (), {"raw": 0})()}
    monkeypatch.setattr(window.confirm, "ask", lambda *_a: True)
    held = []

    def run(job, done=None):
        held.append(window.ctx.pause.holders)
        done(job(), None)

    monkeypatch.setattr(window.canopen, "background", run)
    tab.image = Image([Segment(0, b"firmware")])
    tab._download()
    assert held == [["CANopen firmware download"]]
    assert not window.ctx.pause.paused, "given back when it is done"


def test_a_failed_download_gives_the_pause_back_too(window, tab, monkeypatch):
    on_node_5(window, tab, monkeypatch)  # no 0x1F51 at all: the first write fails
    monkeypatch.setattr(window.confirm, "ask", lambda *_a: True)
    in_line(window, monkeypatch)
    tab.image = Image([Segment(0, b"firmware")])
    tab._download()
    assert not window.ctx.pause.paused and not tab._busy


def test_an_image_that_will_not_read_is_reported(window, tab, tmp_path):
    bad = tmp_path / "not-an-image.hex"
    bad.write_text("this is not Intel HEX\n", encoding="utf-8")
    tab.path.setText(str(bad))
    tab._reload_image()
    assert tab.image is None
    assert tab.summary.text()


def test_what_the_plugin_remembered_is_kept(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    from pycangui.core.context import Context

    ctx = Context(log=print)
    ctx.settings.set("plugins.firmware.transfer", "Block")
    ctx.settings.set("plugins.firmware.programming_timeout_s", 42)
    from pycangui.ui.canopen_firmware import FirmwareTab

    tab = FirmwareTab(None, None, ctx, lambda: None)
    assert tab.transfer.currentText() == "Block"
    assert tab.patience.value() == 42


# --- a workspace that had the plugin installed -------------------------------------------------
PLUGIN = (
    'NAME = "CANopen firmware (CiA 302-3)"\nVERSION = "1.4"\n'
    "def register(app):\n    app.add_pane('main', 'CANopen firmware', lambda _n: None)\n"
)


def _installed(tmp_path, monkeypatch, edited):
    """A workspace with the old plugin in it, as installed or changed since."""
    from pycangui.core import plugin_package
    from pycangui.core.context import Context

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    ctx = Context(log=print)
    folder = ctx.workspace_dir / "plugins" / "firmware"
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    (folder / "program.py").write_text("BLOCK = 1024\n", encoding="utf-8")
    ctx.settings.set(
        "plugins.as_installed", {"firmware": plugin_package.folder_fingerprint(folder)}
    )
    if edited:
        (folder / "program.py").write_text("BLOCK = 7  # ours\n", encoding="utf-8")
    ctx.settings.save()
    return folder


def test_an_untouched_copy_of_the_plugin_is_removed(app, tmp_path, monkeypatch):
    folder = _installed(tmp_path, monkeypatch, edited=False)
    win = MainWindow()
    try:
        assert not folder.exists()
        assert "firmware" not in win.plugins.loaded
    finally:
        win.close()


def test_an_edited_copy_is_kept_and_said_once(app, tmp_path, monkeypatch):
    folder = _installed(tmp_path, monkeypatch, edited=True)
    win = MainWindow()
    try:
        assert (folder / "program.py").read_text(encoding="utf-8").startswith("BLOCK = 7")
        assert "firmware" in win.ctx.settings.get("plugins.built_in_said")
        assert win.plugin_actions.retire_built_in() == [], "said once"
    finally:
        win.close()
