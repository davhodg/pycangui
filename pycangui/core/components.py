# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Replaceable components: the parts of pycangui a file of your own can add
to or stand in for.

Three kinds:

* a **CAN interface** -- an adapter python-can does not support, reached
  through its maker's library. Listed in the Connect bar.
* an **ISO-TP transport** -- what UDS is carried on. Chosen in the UDS pane.
* an **XCP or CCP engine** -- what talks to a calibration slave. Chosen in
  the XCP pane.

Everything else in each protocol -- the pane, the threading, the settings --
stays put. A component is the small part underneath that actually talks on
the wire, with a narrow interface, and that is what makes it replaceable.

pycangui's own components are built in and are not files you will find
anywhere. The components folder holds only yours: a file there *adds* one,
or *replaces* a built-in one by registering the same kind and name. So an
empty folder is the normal state, not a missing installation, and the folder
carries a README saying so for as long as it has nothing else in it.

    from pycangui.core.components import register_component
    from pycangui.xcp.engine import XcpEngine

    @register_component("xcp", "rust", "XCP engine from my Rust library")
    class RustXcp(XcpEngine):
        ...

A CAN interface is a python-can bus class, registered with
:func:`register_interface` rather than :func:`register_component`, because
python-can is what opens every bus and has to be able to find it by name.
That goes into python-can's own table as well as this one.

A file that fails to import is reported in the Event Log and skipped,
exactly like a broken hook file; the built-in components keep working.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The module name user files are imported under, so a registration can be
#: told apart from one of pycangui's own.
USER_PACKAGE = "pycangui_user_components"

INTERFACE = "interface"

#: What each kind is, in the words the panes use for it, in report order.
KINDS = {
    INTERFACE: "CAN interface (Connect bar)",
    "isotp": "ISO-TP transport (UDS pane, Transport)",
    "xcp": "XCP and CCP engine (XCP pane, Engine)",
}

#: What an empty CAN-interface section means, which is not "none exist".
NO_INTERFACES = "none added -- python-can's own are in the Connect bar"

README_NAME = "README.txt"
README = """\
Your own pycangui components go in this folder.

It is empty until you add something, and that is normal: pycangui's own
components are built in, not kept here. A Python file in this folder either
ADDS a component or REPLACES a built-in one, by registering the same kind
and name.

Three kinds can be added or replaced:

  CAN interface       an adapter python-can does not support, through its
                      maker's library -- register_interface(name, description)
  ISO-TP transport    what UDS is carried on -- register_component("isotp", ...)
  XCP or CCP engine   what talks to a calibration slave
                      -- register_component("xcp", ...)

Tools > List components prints what is registered, where each came from and
which one is in use. Files are read when pycangui starts. A file whose name
starts with an underscore is ignored, and so is this one.

This README is written only while the folder holds none of your own files.
"""


@dataclass(frozen=True)
class ComponentSpec:
    kind: str
    name: str
    factory: Callable[..., Any]
    description: str = ""
    #: The file that registered it, or "" where that cannot be found.
    source: str = ""
    #: What it stands in for, where it replaced something: "" when it
    #: replaced nothing, "a built-in one", or "python-can's own".
    replaces: str = ""

    @property
    def built_in(self) -> bool:
        """Shipped with pycangui, as against added from the folder."""
        return not getattr(self.factory, "__module__", "").startswith(USER_PACKAGE)


def _source_of(factory: Callable[..., Any]) -> str:
    try:
        return inspect.getsourcefile(factory) or ""
    except (TypeError, OSError):  # a builtin, or something made at run time
        return ""


@dataclass
class ComponentRegistry:
    _by_kind: dict[str, dict[str, ComponentSpec]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)  # user file -> traceback
    #: The user files imported on the last load, whether or not they
    #: registered anything.
    loaded: list[str] = field(default_factory=list)

    def register(
        self,
        kind: str,
        name: str,
        factory: Callable[..., Any],
        description: str = "",
        replaces: str = "",
    ) -> None:
        before = self._by_kind.get(kind, {}).get(name)
        if not replaces and before is not None and before.built_in:
            replaces = "a built-in one"
        spec = ComponentSpec(kind, name, factory, description, _source_of(factory), replaces)
        self._by_kind.setdefault(kind, {})[name] = spec

    def kinds(self) -> list[str]:
        return list(self._by_kind)

    def names(self, kind: str) -> list[str]:
        return list(self._by_kind.get(kind, {}))

    def specs(self, kind: str) -> list[ComponentSpec]:
        return list(self._by_kind.get(kind, {}).values())

    def get(self, kind: str, name: str) -> ComponentSpec | None:
        return self._by_kind.get(kind, {}).get(name)

    def create(self, kind: str, name: str, *args: Any, **kwargs: Any) -> Any:
        """Build a component. Falls back to the first registered one if
        *name* is unknown (e.g. one of yours that failed to load this run)."""
        spec = self.get(kind, name)
        if spec is None:
            available = self.specs(kind)
            if not available:
                raise LookupError(f"no {kind} component registered")
            spec = available[0]
        return spec.factory(*args, **kwargs)

    def load_user_components(
        self,
        folder: Path,
        log: Callable[[str], None],
        warn: Callable[[str], None] | None = None,
    ) -> None:
        """Import every module in *folder* so it can register components.

        Two sinks, because the two things this says are not the same
        thing: "loaded x.py" is a note and "x.py failed to load" is a
        warning, and sending both to one place made every successful
        load look like a problem.

        Each module is kept in ``sys.modules``. A CAN interface needs it:
        python-can finds the bus class by importing the module by name when
        a bus is opened, and that only works for one already imported.
        """
        warn = warn or log
        self.errors.clear()
        self.loaded.clear()
        if not folder.is_dir():
            return
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # keep __pycache__ out of the user's folder
        try:
            for path in sorted(folder.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                name = f"{USER_PACKAGE}.{path.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(name, path)
                    module = importlib.util.module_from_spec(spec)
                    sys.modules[name] = module
                    spec.loader.exec_module(module)
                except Exception:  # user code may fail in any way
                    sys.modules.pop(name, None)
                    self.errors[str(path)] = traceback.format_exc()
                    warn(f"Component file {path} failed to load:\n{traceback.format_exc()}")
                else:
                    self.loaded.append(str(path))
                    log(f"Loaded component file {path.name}")
        finally:
            sys.dont_write_bytecode = saved

    def report(self, in_use: dict[str, str], folder: Path | None = None) -> list[str]:
        """What is registered, where each came from, and which is in use.

        Lines rather than a dialog: it goes to the Event Log, where it can
        be read, copied into a bug report and compared with the next run.

        Built-in and yours are told apart on every line, and the folder is
        reported separately, because the two are easily confused: a report
        full of components beside an empty folder reads as though the
        folder had lost them.
        """
        lines = ["Components -- * marks the one in use"]
        for kind in [*KINDS, *sorted(k for k in self._by_kind if k not in KINDS)]:
            specs = self.specs(kind)
            if kind != INTERFACE and not specs:
                continue
            chosen = in_use.get(kind, "")
            lines.append(f"  {KINDS.get(kind, kind)}")
            if not specs:
                lines.append(f"      {NO_INTERFACES}")
            for spec in specs:
                mark = "*" if spec.name == chosen else " "
                about = f" -- {spec.description}" if spec.description else ""
                if spec.built_in:
                    where = "built in"
                else:
                    where = f"yours, {Path(spec.source).name}" if spec.source else "yours"
                    if spec.replaces:
                        where += f", replacing {spec.replaces}"
                lines.append(f"    {mark} {spec.name}{about} ({where})")
            names = self.names(kind)
            if chosen and kind != INTERFACE and chosen not in names:
                fallback = names[0] if names else "nothing"
                lines.append(f"      {chosen} is chosen but not registered, so {fallback} is used")
        if folder is not None:
            lines.append(f"Your components folder: {folder}")
            sources = {s.source for specs in self._by_kind.values() for s in specs.values()}
            for path in self.loaded:
                said = "" if path in sources else " -- loaded, but registered nothing"
                lines.append(f"    {Path(path).name}{said}")
            for path, trace in self.errors.items():
                last = trace.strip().splitlines()[-1] if trace.strip() else "failed"
                lines.append(f"    {Path(path).name} -- failed to load: {last}")
            if not self.loaded and not self.errors:
                lines.append(
                    "    empty -- the components above are built in; a file here adds "
                    "one or replaces one"
                )
        return lines


COMPONENTS = ComponentRegistry()


def register_component(kind: str, name: str, description: str = "") -> Callable[[type], type]:
    """Class decorator that registers a component implementation."""

    def decorate(cls: type) -> type:
        COMPONENTS.register(kind, name, cls, description)
        return cls

    return decorate


def register_interface(name: str, description: str = "") -> Callable[[type], type]:
    """Class decorator for a CAN interface: a python-can bus class.

    Registered twice, on purpose. Here, so it is reported with the file it
    came from. And in python-can's own table, because python-can is what
    opens every bus -- pycangui calls ``can.Bus(interface=name)`` -- and it
    looks the name up there when the bus is opened.

    A name python-can already has is replaced, which is how a patched
    driver for an adapter it does support would be used; the report says
    so.
    """

    def decorate(cls: type) -> type:
        import can.interfaces  # heavy, so only when somebody adds an interface

        replaces = "python-can's own" if name in can.interfaces.BACKENDS else ""
        can.interfaces.BACKENDS[name] = (cls.__module__, cls.__name__)
        refresh_interface_list()
        COMPONENTS.register(INTERFACE, name, cls, description, replaces)
        return cls

    return decorate


def refresh_interface_list() -> None:
    """Bring python-can's list of interface names up to date with its table.

    python-can checks a name twice when a bus is opened: against
    ``VALID_INTERFACES`` first, and only then looks it up in ``BACKENDS``.
    The first is a frozenset built once, when python-can is imported, and
    three modules each hold their own reference to it -- so an entry added
    to the table afterwards is refused as "Unknown interface type" before
    the table is ever read. Rebuilding the set from the table gives exactly
    what python-can would have built itself had the adapter been an
    installed plugin; the test that opens a bus through ``can.Bus`` is what
    would say so if a later python-can moved the check again.
    """
    import can
    import can.interfaces
    import can.util

    valid = frozenset(sorted(can.interfaces.BACKENDS))
    for module in (can.interfaces, can.util, can):
        module.VALID_INTERFACES = valid


def write_readme(folder: Path) -> None:
    """Explain the folder, while it has nothing of yours in it.

    Written only then, and never over an existing file: once you have added
    a component the README has done its job, and one you deleted should
    stay deleted.
    """
    if not folder.is_dir() or (folder / README_NAME).exists():
        return
    if any(folder.glob("*.py")):
        return
    try:
        (folder / README_NAME).write_text(README, encoding="utf-8")
    except OSError:
        pass  # a read-only folder is not worth an error over an explanation


#: What the settings used to call these, so a choice made before the rename
#: is carried across rather than quietly reset.
_OLD_FOLDER = "backends"
_OLD_KEY = "backends."
KEY = "components."


def carry_over(user_dir: Path, settings) -> list[str]:
    """Move anything kept under the old name, once, so nothing is lost.

    The folder was ``backends`` and the settings keys ``backends.<kind>``.
    A file somebody wrote and a Transport or Engine somebody chose are both
    theirs, so they are moved rather than left behind -- and moved, not
    read from both places, so there is one name afterwards. Returns what
    was moved, for the log.
    """
    moved = []
    old = user_dir / _OLD_FOLDER
    new = user_dir / "components"
    if old.is_dir():
        ours = [p for p in new.iterdir() if p.name != README_NAME] if new.is_dir() else []
        if not ours:
            if new.is_dir():
                (new / README_NAME).unlink(missing_ok=True)
                new.rmdir()
            old.rename(new)
            moved.append(f"moved {old} to {new}")
        else:
            # Both have files: merging somebody's code is not a guess to
            # make on their behalf, so say so and leave both where they are.
            moved.append(f"left {old} alone: {new} already has files in it")
    for kind in ("isotp", "xcp"):
        value = settings.get(_OLD_KEY + kind)
        if value is not None:
            if settings.get(KEY + kind) is None:
                settings.set(KEY + kind, value)
            settings.remove(_OLD_KEY + kind)
            moved.append(f"kept the {kind} choice, {value}")
    return moved
