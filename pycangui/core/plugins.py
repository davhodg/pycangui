"""User code that adds screens, not just answers.

A hook answers a question pycangui already knows to ask -- which EDS, what to
call this node -- from a fixed list of them.  A plugin is the other half: code
that adds something that was not there, a pane of its own with its own buttons
doing something pycangui has never heard of.

The contract is deliberately small.  A plugin is a folder with a ``plugin.py``
in it that declares a name and a ``register(app)`` function, and everything it
can do it does through the ``app`` it is handed.  That object is the whole API,
which means the API is one thing to document, one thing to keep stable, and one
thing to widen when a plugin needs something it has not got.

Two properties are worth more than the features:

**A plugin that fails takes only itself down.**  Loading is per plugin and the
traceback goes to the Event Log, where somebody will see it, rather than to a
stderr nobody is watching -- pycangui runs under pythonw, which has no console
at all.

**Reload means reload.**  Everything a plugin adds is recorded against it, so
loading it again removes the pane, the menu entries and the buttons the last
version put there instead of leaving a second copy beside them.  Editing a
plugin and pressing reload is how one gets written; "please restart" is how one
does not.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: What ``app`` offers.  A plugin declares the version it was written against
#: and is refused if it is newer than this -- a plugin from the future asking
#: for methods that do not exist yet fails in the middle of doing something,
#: which is a worse way to find out.
API_VERSION = 1

#: The file inside a plugin folder.  A folder rather than a single file so that
#: a plugin can bring its own modules, icons and data with it.
ENTRY = "plugin.py"


def builtin_dir() -> Path:
    """The plugins pycangui ships with.

    Shipped as plugins rather than compiled into the window on purpose: what
    they do is not part of any protocol pycangui speaks, and building them
    through the same door a user's plugin goes through is the only way to find
    out whether that door is wide enough.
    """
    return Path(__file__).resolve().parent.parent / "plugins"


@dataclass
class Loaded:
    """One plugin, and how it went."""

    name: str  # the folder name, which is what identifies it
    path: Path
    title: str = ""  # what it calls itself
    description: str = ""
    error: str = ""
    app: Any = None
    builtin: bool = False

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def label(self) -> str:
        return self.title or self.name


@dataclass
class Plugins:
    """Everything found, loaded and still loaded."""

    #: Where to look, in order.  A user's plugin replaces a shipped one of the
    #: same name, the same way a user's hook file replaces the default.
    folders: list[Path] = field(default_factory=list)
    make_app: Callable[[str], Any] | None = None
    log: Callable[[str], None] = print
    warn: Callable[[str], None] = print
    loaded: dict[str, Loaded] = field(default_factory=dict)

    # --- finding them --------------------------------------------------------------
    def found(self) -> dict[str, Path]:
        """Plugin folder name -> its entry file, later folders winning."""
        out: dict[str, Path] = {}
        for folder in self.folders:
            if not folder.is_dir():
                continue
            for child in sorted(folder.iterdir()):
                entry = child / ENTRY
                if child.is_dir() and not child.name.startswith(("_", ".")) and entry.is_file():
                    out[child.name] = entry
        return out

    # --- loading them ---------------------------------------------------------------
    def load_all(self) -> None:
        """Unload whatever is loaded, then load what is there now."""
        self.unload_all()
        builtin = {name for name, path in self.found().items() if self._is_builtin(path)}
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # keep __pycache__ out of the user's folders
        try:
            for name, entry in self.found().items():
                self._load_one(name, entry, builtin=name in builtin)
        finally:
            sys.dont_write_bytecode = saved
        self._report()

    def _is_builtin(self, entry: Path) -> bool:
        return bool(self.folders) and entry.is_relative_to(self.folders[0])

    def _load_one(self, name: str, entry: Path, builtin: bool = False) -> None:
        record = Loaded(name=name, path=entry, builtin=builtin)
        self.loaded[name] = record
        module = self._import(record)
        if module is None:
            return

        wanted = getattr(module, "API_VERSION", 1)
        if not isinstance(wanted, int) or wanted > API_VERSION:
            record.error = (
                f"written for plugin API {wanted}, and this is version {API_VERSION}. "
                "A newer pycangui will run it."
            )
            return
        record.title = str(getattr(module, "NAME", "") or name)
        record.description = str(getattr(module, "DESCRIPTION", "") or "")

        register = getattr(module, "register", None)
        if not callable(register):
            record.error = f"{ENTRY} has no register(app) function, so it can do nothing."
            return
        if self.make_app is None:
            return  # discovery only, with nothing to register against
        record.app = self.make_app(name)
        try:
            register(record.app)
        except Exception:
            record.error = traceback.format_exc()
            # Half of it may have been added before it failed.  Taking that
            # back is the difference between a broken plugin and a broken
            # window with a menu entry that raises whenever it is used.
            self._undo(record)

    def _import(self, record: Loaded):
        try:
            spec = importlib.util.spec_from_file_location(
                f"pycangui_plugins.{record.name}", record.path
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            # Registered before it is executed, so that a plugin split across
            # several files can import its own siblings.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module
        except Exception:  # user code fails in every way there is
            record.error = traceback.format_exc()
            return None

    # --- unloading them ---------------------------------------------------------------
    def unload_all(self) -> None:
        for record in list(self.loaded.values()):
            self._undo(record)
        self.loaded.clear()

    def _undo(self, record: Loaded) -> None:
        if record.app is None:
            return
        try:
            record.app.remove_all()
        except Exception:
            self.warn(f"Plugin {record.label} left something behind:\n{traceback.format_exc()}")
        sys.modules.pop(f"pycangui_plugins.{record.name}", None)

    # --- what happened ------------------------------------------------------------------
    def errors(self) -> dict[str, str]:
        return {name: r.error for name, r in self.loaded.items() if r.error}

    def working(self) -> list[Loaded]:
        return [r for r in self.loaded.values() if r.ok]

    def _report(self) -> None:
        good = self.working()
        if good:
            self.log(f"Plugins loaded: {', '.join(sorted(r.label for r in good))}")
        for name, error in self.errors().items():
            self.warn(f"Plugin {name} failed to load:\n{error}")
