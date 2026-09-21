# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The component registry, and one of your own replacing a built-in one."""

import importlib
import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.core.bus import BusManager
from pycangui.core.components import COMPONENTS, ComponentRegistry
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp.manager import XcpManager


def test_registry_basics():
    r = ComponentRegistry()
    r.register("thing", "a", lambda: "A", "first")
    r.register("thing", "b", lambda: "B")
    assert r.names("thing") == ["a", "b"]
    assert r.get("thing", "a").description == "first"
    assert r.create("thing", "b") == "B"
    assert r.create("thing", "missing") == "A"  # unknown name falls back
    with pytest.raises(LookupError):
        r.create("nothing", "x")


def test_builtin_components_registered():
    # importing the modules is what registers them
    importlib.import_module("pycangui.uds.transport")
    importlib.import_module("pycangui.xcp.engine")
    assert "xcp-builtin" in COMPONENTS.names("xcp")
    assert "can-isotp" in COMPONENTS.names("isotp")


USER_COMPONENT = """
from pycangui.core.components import register_component
from pycangui.xcp import ConnectInfo
from pycangui.xcp.engine import XcpEngine


@register_component("xcp", "fake", "test double")
class FakeEngine(XcpEngine):
    def __init__(self, bus, ctx=None):
        self.memory = bytearray(0x3000)
        self.memory[0x1002:0x1004] = (999).to_bytes(2, "little")
        self.info = None

    def connect(self):
        self.info = ConnectInfo(0x01, 0, False, 8, 8)
        return self.info

    def disconnect(self):
        self.info = None

    def get_seed(self, resource):
        return b"\\x01\\x02"

    def unlock(self, key):
        pass

    def read(self, address, size):
        return bytes(self.memory[address : address + size])

    def write(self, address, data):
        self.memory[address : address + len(data)] = data
"""


def wait_until(pred, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not pred():
        QCoreApplication.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.005)


def test_user_component_replaces_builtin(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    (ctx.components_dir / "fake_xcp.py").write_text(USER_COMPONENT)
    logged: list[str] = []
    COMPONENTS.load_user_components(ctx.components_dir, logged.append)
    assert not COMPONENTS.errors and "fake" in COMPONENTS.names("xcp")

    ctx.settings.set("components.xcp", "fake")
    bus = BusManager()
    hub = SignalHub()
    manager = XcpManager(bus, Hooks(ctx), hub, ctx)
    lines: list[str] = []
    manager.result.connect(lines.append)
    assert manager.component_name == "fake"

    manager.load_a2l(str(resources.path("demo.a2l")))
    n = len(lines)
    manager.connect_slave()  # no bus, no demo slave: the fake engine answers
    wait_until(lambda: len(lines) > n)
    assert "(fake)" in lines[-1]
    n = len(lines)
    manager.read("BatteryVoltage")
    wait_until(lambda: len(lines) > n)
    assert lines[-1] == "BatteryVoltage = 9.99 V"

    manager.set_component("xcp-builtin")  # switching back works and is remembered
    assert ctx.settings.get("components.xcp") == "xcp-builtin"
    manager.shutdown()


def test_broken_user_component_is_isolated(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    (ctx.components_dir / "broken.py").write_text("import nonexistent_module_xyz\n")
    logged: list[str] = []
    COMPONENTS.load_user_components(ctx.components_dir, logged.append)
    assert len(COMPONENTS.errors) == 1
    assert any("failed to load" in line for line in logged)
    assert "xcp-builtin" in COMPONENTS.names("xcp")  # built-in still usable


def test_an_engine_that_does_not_exist_says_so(app, tmp_path, monkeypatch):
    """A name from before a rename, or one of your own that failed to load.
    Falling back is right; doing it quietly is not, because connecting with
    the wrong protocol looks like a broken slave."""
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.core.signals import SignalHub
    from pycangui.xcp.manager import XcpManager

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    ctx.settings.set("components.xcp", "nonesuch")
    manager = XcpManager(BusManager(), Hooks(ctx), SignalHub(), ctx)
    assert manager.component_name == "xcp-builtin", "it carries on with one that exists"

    # Again, with somewhere for the message to go this time: the first one
    # is said while the manager is being built, before anything can listen.
    said: list[str] = []
    manager.result.connect(said.append)
    manager.component_name = "nonesuch"
    manager._make_engine()

    assert any("nonesuch" in line for line in said), "and names the one it could not find"
    manager.shutdown()


def test_the_engine_chosen_last_time_is_the_one_that_opens(app, tmp_path, monkeypatch):
    from pycangui.core.context import Context
    from pycangui.core.hooks import Hooks
    from pycangui.core.signals import SignalHub
    from pycangui.xcp.manager import XcpManager

    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    ctx.settings.set("components.xcp", "ccp-builtin")
    manager = XcpManager(BusManager(), Hooks(ctx), SignalHub(), ctx)

    assert manager.protocol == "CCP"
    manager.shutdown()
