# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What is registered, and saying so -- plus a CAN interface from the folder.

Each test gets a registry of its own, so what one registers cannot leak into
the next; the CAN-interface tests also put python-can's table back, since
that one is global by design.
"""

from __future__ import annotations

import sys

import can
import can.interfaces
import pytest

from pycangui.core import components
from pycangui.core.bus import available_interfaces
from pycangui.core.components import (
    INTERFACE,
    NO_INTERFACES,
    README_NAME,
    USER_PACKAGE,
    ComponentRegistry,
    carry_over,
    refresh_interface_list,
    write_readme,
)


@pytest.fixture
def registry(monkeypatch):
    """A registry of its own, standing in for the global one."""
    fresh = ComponentRegistry()
    monkeypatch.setattr(components, "COMPONENTS", fresh)
    return fresh


@pytest.fixture
def folder(tmp_path):
    out = tmp_path / "components"
    out.mkdir()
    return out


def built_in(registry):
    """Two built-in components, as pycangui's own would be."""

    class IsoTp:
        pass

    class Xcp:
        pass

    registry.register("isotp", "can-isotp", IsoTp, "ISO 15765-2 from can-isotp")
    registry.register("xcp", "xcp-builtin", Xcp, "XCP on CAN")


def load(registry, folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")
    said = []
    registry.load_user_components(folder, said.append)
    return said


# --- the report ----------------------------------------------------------------------------
def test_the_report_marks_built_in_and_the_one_in_use(registry, folder):
    built_in(registry)
    text = "\n".join(registry.report({"isotp": "can-isotp"}, folder))
    assert "* can-isotp" in text, "the one in use is not marked"
    assert "(built in)" in text
    assert "  xcp-builtin" in text and "* xcp-builtin" not in text


def test_an_empty_folder_is_said_to_be_normal(registry, folder):
    """Reported: a list of components beside an empty folder reads as though
    the folder had lost them."""
    built_in(registry)
    text = "\n".join(registry.report({}, folder))
    assert "empty -- the components above are built in" in text


def test_no_added_interface_is_not_no_interfaces(registry, folder):
    """python-can's own adapters are in the Connect bar, not here."""
    built_in(registry)
    text = "\n".join(registry.report({}, folder))
    assert "CAN interface" in text
    assert NO_INTERFACES in text


def test_one_of_yours_that_replaces_a_built_in_says_so(registry, folder, monkeypatch):
    built_in(registry)
    monkeypatch.setattr(components, "COMPONENTS", registry)
    load(
        registry,
        folder,
        "mine.py",
        "from pycangui.core.components import register_component\n"
        "@register_component('isotp', 'can-isotp', 'my own')\n"
        "class Mine:\n"
        "    pass\n",
    )
    text = "\n".join(registry.report({"isotp": "can-isotp"}, folder))
    assert "yours, mine.py, replacing a built-in one" in text
    assert not registry.get("isotp", "can-isotp").built_in


def test_a_file_that_registers_nothing_is_named(registry, folder):
    """The one mistake nothing else would ever mention."""
    load(registry, folder, "forgot.py", "x = 1\n")
    text = "\n".join(registry.report({}, folder))
    assert "forgot.py -- loaded, but registered nothing" in text


def test_a_file_that_failed_says_why(registry, folder):
    load(registry, folder, "broken.py", "import no_such_module_anywhere\n")
    text = "\n".join(registry.report({}, folder))
    assert "broken.py -- failed to load" in text
    assert "no_such_module_anywhere" in text


def test_a_choice_that_is_not_registered_says_what_is_used_instead(registry, folder):
    built_in(registry)
    text = "\n".join(registry.report({"xcp": "gone"}, folder))
    assert "gone is chosen but not registered, so xcp-builtin is used" in text


# --- a CAN interface from the folder ---------------------------------------------------------
ADAPTER = """
import can
from pycangui.core.components import register_interface


@register_interface("{name}", "a pretend adapter")
class PretendBus(can.BusABC):
    def __init__(self, channel=None, **kwargs):
        super().__init__(channel=channel, **kwargs)
        self.channel_info = "pretend"
        self.sent = []

    def send(self, msg, timeout=None):
        self.sent.append(msg)

    def _recv_internal(self, timeout):
        return None, False

    def shutdown(self):
        super().shutdown()
"""


@pytest.fixture
def adapter(registry, folder, monkeypatch):
    """A CAN interface added from the folder, taken out of python-can again."""
    monkeypatch.setattr(components, "COMPONENTS", registry)
    name = "pretendusb"
    load(registry, folder, "pretend.py", ADAPTER.format(name=name))
    yield name
    can.interfaces.BACKENDS.pop(name, None)
    refresh_interface_list()
    sys.modules.pop(f"{USER_PACKAGE}.pretend", None)


def test_an_added_interface_is_in_the_connect_bar(adapter):
    assert adapter in available_interfaces()


def test_python_can_opens_it_by_name(adapter):
    """The whole point: pycangui opens every bus through can.Bus, and it
    has to find the class there."""
    bus = can.Bus(interface=adapter, channel="0")
    try:
        assert type(bus).__name__ == "PretendBus"
        bus.send(can.Message(arbitration_id=0x123, data=b"\x01"))
        assert bus.sent[0].arbitration_id == 0x123
    finally:
        bus.shutdown()


def test_python_can_is_told_the_name_as_well_as_the_class(adapter):
    """can.Bus checks the name against VALID_INTERFACES before it looks in
    the table, so the table alone was refused as an unknown interface."""
    import can.util

    assert adapter in can.util.VALID_INTERFACES
    assert adapter in can.VALID_INTERFACES


def test_it_is_reported_as_yours(adapter, registry, folder):
    text = "\n".join(registry.report({INTERFACE: adapter}, folder))
    assert f"* {adapter} -- a pretend adapter (yours, pretend.py)" in text


def test_replacing_one_of_python_cans_own_says_so(registry, folder, monkeypatch):
    """A patched driver for an adapter python-can does support."""
    monkeypatch.setattr(components, "COMPONENTS", registry)
    original = can.interfaces.BACKENDS["virtual"]
    try:
        load(registry, folder, "patched.py", ADAPTER.format(name="virtual"))
        assert registry.get(INTERFACE, "virtual").replaces == "python-can's own"
    finally:
        can.interfaces.BACKENDS["virtual"] = original
        refresh_interface_list()
        sys.modules.pop(f"{USER_PACKAGE}.patched", None)


# --- the README, and the move from the old name --------------------------------------------
def test_an_empty_folder_gets_a_readme_saying_so(folder):
    write_readme(folder)
    text = (folder / README_NAME).read_text(encoding="utf-8")
    assert "empty until you add something" in text


def test_the_readme_is_not_written_once_you_have_files(folder):
    (folder / "mine.py").write_text("x = 1\n", encoding="utf-8")
    write_readme(folder)
    assert not (folder / README_NAME).exists()


def test_a_readme_you_changed_is_left_alone(folder):
    (folder / README_NAME).write_text("mine\n", encoding="utf-8")
    write_readme(folder)
    assert (folder / README_NAME).read_text(encoding="utf-8") == "mine\n"


class Settings(dict):
    def set(self, key, value):
        self[key] = value

    def remove(self, key):
        self.pop(key, None)


def test_the_old_folder_is_moved_with_what_is_in_it(tmp_path):
    (tmp_path / "backends").mkdir()
    (tmp_path / "backends" / "mine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "components").mkdir()  # made empty when the context was built
    said = carry_over(tmp_path, Settings())
    assert (tmp_path / "components" / "mine.py").exists()
    assert not (tmp_path / "backends").exists()
    assert said


def test_a_readme_alone_does_not_stop_the_move(tmp_path):
    (tmp_path / "backends").mkdir()
    (tmp_path / "backends" / "mine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "components").mkdir()
    write_readme(tmp_path / "components")
    assert (tmp_path / "components" / README_NAME).exists()
    carry_over(tmp_path, Settings())
    assert (tmp_path / "components" / "mine.py").exists()


def test_two_folders_with_files_are_both_left_and_it_is_said(tmp_path):
    """Merging somebody's code is not a guess to make for them."""
    for name in ("backends", "components"):
        (tmp_path / name).mkdir()
        (tmp_path / name / f"{name}.py").write_text("x = 1\n", encoding="utf-8")
    said = carry_over(tmp_path, Settings())
    assert (tmp_path / "backends" / "backends.py").exists()
    assert (tmp_path / "components" / "components.py").exists()
    assert any("left" in line for line in said)


def test_a_chosen_transport_and_engine_are_kept(tmp_path):
    settings = Settings({"backends.xcp": "ccp-builtin", "backends.isotp": "mine"})
    carry_over(tmp_path, settings)
    assert settings == {"components.xcp": "ccp-builtin", "components.isotp": "mine"}


def test_nothing_to_move_says_nothing(tmp_path):
    assert carry_over(tmp_path, Settings()) == []
