"""Swappable protocol back ends.

Every protocol in pycangui is split into

* a **manager** -- the GUI-facing object: Qt signals, threading, settings,
  A2L / EDS / DBC handling, feeding the signal hub.  This never changes.
* an **engine** (or transport) -- the part that actually talks the protocol on
  the wire.  This is small, has a narrow interface, and is replaceable.

Engines register themselves here under a *kind* (``"xcp"``, ``"isotp"``, ...)
and a name.  The user picks one per kind; the choice is remembered in
``settings.json`` under ``backends.<kind>``.

Users add their own by dropping a module into ``<user dir>/backends/``:

    from pycangui.core.backends import register_backend
    from pycangui.xcp.engine import XcpEngine

    @register_backend("xcp", "rust", "XCP engine from my Rust library")
    class RustXcp(XcpEngine):
        def __init__(self, bus, ctx):
            self._lib = ctypes.CDLL(str(ctx.user_dir / "xcp_rust.dll"))
        ...

A module that fails to import is reported in the Event Log pane and skipped, exactly
like a broken hook file; the built-in engine keeps working.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BackendSpec:
    kind: str
    name: str
    factory: Callable[..., Any]
    description: str = ""


@dataclass
class BackendRegistry:
    _by_kind: dict[str, dict[str, BackendSpec]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # user file -> traceback

    def register(
        self, kind: str, name: str, factory: Callable[..., Any], description: str = ""
    ) -> None:
        self._by_kind.setdefault(kind, {})[name] = BackendSpec(kind, name, factory, description)

    def names(self, kind: str) -> list[str]:
        return list(self._by_kind.get(kind, {}))

    def specs(self, kind: str) -> list[BackendSpec]:
        return list(self._by_kind.get(kind, {}).values())

    def get(self, kind: str, name: str) -> BackendSpec | None:
        return self._by_kind.get(kind, {}).get(name)

    def create(self, kind: str, name: str, *args: Any, **kwargs: Any) -> Any:
        """Build an engine.  Falls back to the first registered one if *name*
        is unknown (e.g. a user backend that failed to load this run)."""
        spec = self.get(kind, name)
        if spec is None:
            available = self.specs(kind)
            if not available:
                raise LookupError(f"no {kind} backend registered")
            spec = available[0]
        return spec.factory(*args, **kwargs)

    def load_user_backends(
        self,
        folder: Path,
        log: Callable[[str], None],
        warn: Callable[[str], None] | None = None,
    ) -> None:
        """Import every module in *folder* so it can register back ends.

        Two sinks, because the two things this says are not the same
        thing: "loaded x.py" is a note and "x.py failed to load" is a
        warning, and sending both to one place made every successful
        load look like a problem.
        """
        warn = warn or log
        self.errors.clear()
        if not folder.is_dir():
            return
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # keep __pycache__ out of the user's folder
        try:
            for path in sorted(folder.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                try:
                    spec = importlib.util.spec_from_file_location(
                        f"pycangui_user_backends.{path.stem}", path
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                except Exception:  # user code may fail in any way
                    self.errors[str(path)] = traceback.format_exc()
                    warn(f"Backend file {path} failed to load:\n{traceback.format_exc()}")
                else:
                    log(f"Loaded backend module {path.name}")
        finally:
            sys.dont_write_bytecode = saved


BACKENDS = BackendRegistry()


def register_backend(kind: str, name: str, description: str = "") -> Callable[[type], type]:
    """Class decorator that registers an engine implementation."""

    def decorate(cls: type) -> type:
        BACKENDS.register(kind, name, cls, description)
        return cls

    return decorate
