# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The backend registry, and a user-supplied engine replacing a built-in one."""

import importlib
import time

import pytest
from PySide6.QtCore import QCoreApplication

from pycangui import resources
from pycangui.core.backends import BACKENDS, BackendRegistry
from pycangui.core.bus import BusManager
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.core.signals import SignalHub
from pycangui.xcp.manager import XcpManager


def test_registry_basics():
    r = BackendRegistry()
    r.register("thing", "a", lambda: "A", "first")
    r.register("thing", "b", lambda: "B")
    assert r.names("thing") == ["a", "b"]
    assert r.get("thing", "a").description == "first"
    assert r.create("thing", "b") == "B"
    assert r.create("thing", "missing") == "A"  # unknown name falls back
    with pytest.raises(LookupError):
        r.create("nothing", "x")


def test_builtin_backends_registered():
    # importing the modules is what registers them
    importlib.import_module("pycangui.uds.transport")
    importlib.import_module("pycangui.xcp.engine")
    assert "native" in BACKENDS.names("xcp")
    assert "can-isotp" in BACKENDS.names("isotp")


USER_BACKEND = """
from pycangui.core.backends import register_backend
from pycangui.xcp import ConnectInfo
from pycangui.xcp.engine import XcpEngine


@register_backend("xcp", "fake", "test double")
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


def test_user_backend_replaces_builtin(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    (ctx.backends_dir / "fake_xcp.py").write_text(USER_BACKEND)
    logged: list[str] = []
    BACKENDS.load_user_backends(ctx.backends_dir, logged.append)
    assert not BACKENDS.errors and "fake" in BACKENDS.names("xcp")

    ctx.settings.set("backends.xcp", "fake")
    bus = BusManager()
    hub = SignalHub()
    manager = XcpManager(bus, Hooks(ctx), hub, ctx)
    lines: list[str] = []
    manager.result.connect(lines.append)
    assert manager.backend_name == "fake"

    manager.load_a2l(str(resources.path("demo.a2l")))
    n = len(lines)
    manager.connect_slave()  # no bus, no demo slave: the fake engine answers
    wait_until(lambda: len(lines) > n)
    assert "(fake)" in lines[-1]
    n = len(lines)
    manager.read("BatteryVoltage")
    wait_until(lambda: len(lines) > n)
    assert lines[-1] == "BatteryVoltage = 9.99 V"

    manager.set_backend("native")  # switching back works and is remembered
    assert ctx.settings.get("backends.xcp") == "native"
    manager.shutdown()


def test_broken_user_backend_is_isolated(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    ctx = Context(log=print)
    (ctx.backends_dir / "broken.py").write_text("import nonexistent_module_xyz\n")
    logged: list[str] = []
    BACKENDS.load_user_backends(ctx.backends_dir, logged.append)
    assert len(BACKENDS.errors) == 1
    assert any("failed to load" in line for line in logged)
    assert "native" in BACKENDS.names("xcp")  # built-in still usable
