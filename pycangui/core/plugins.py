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

**Nothing is a plugin until it is installed.**  The ones pycangui ships with
are a catalogue rather than a load path: they sit in the package until somebody
asks for one, at which point a copy goes into the workspace and is loaded from
there like any other.  Anything else would put a screen nobody asked for into
every window -- and would leave the shipped ones as the one sort of plugin you
could not edit, because editing them would mean editing the installation.

An installed plugin can also be switched off without being thrown away.
Inactive means *not loaded at all*: no pane, no menu entries, no toolbar
buttons, and none of its code running.  That is the useful sense of off -- one
that leaves the window exactly as it would be if the plugin were not there,
while keeping whatever you had edited into it.

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

import ast
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
#: a plugin can bring its own modules, icons and data with it.  A single file
#: is what it is *distributed* as -- see ``plugin_package`` -- which is a
#: different question from what it is while it is installed.
ENTRY = "plugin.py"

#: What a plugin says about itself when it says nothing.  A version matters
#: most for the ones that travel: the copy in your workspace is yours, and the
#: only way to know which of ours it started life as is for it to say.
NO_VERSION = "0"


def builtin_dir() -> Path:
    """The plugins pycangui ships with, as a catalogue to install *from*.

    Shipped as plugins rather than compiled into the window on purpose: what
    they do is not part of any protocol pycangui speaks, and building them
    through the same door a user's plugin goes through is the only way to find
    out whether that door is wide enough.
    """
    return Path(__file__).resolve().parent.parent / "plugins"


@dataclass(frozen=True)
class Info:
    """What a ``plugin.py`` says about itself, read without running it.

    Read rather than imported, because this is asked about plugins that have
    not been chosen -- the ones in the catalogue, and the ones switched off.
    Importing a file to find out what it calls itself would run it, and the
    whole point of *not installed* and *inactive* is that the code does not
    run.
    """

    title: str = ""
    description: str = ""
    version: str = NO_VERSION
    api_version: int = 1


#: The module-level names read out of a plugin file without running it.
WANTED = ("NAME", "DESCRIPTION", "VERSION", "API_VERSION")


def describe(entry: Path) -> Info:
    """``NAME``, ``DESCRIPTION`` and ``VERSION`` out of a plugin file."""
    try:
        return describe_source(entry.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return Info()


def describe_source(source: str) -> Info:
    """The same, for a file that is still inside a package nobody has opened.

    Anything it cannot make sense of is left at its default: a plugin that
    computes its own name is welcome to, and will simply be listed under its
    folder name until it is loaded.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Info()
    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in WANTED:
                try:
                    found[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    api = found.get("API_VERSION", 1)
    return Info(
        title=str(found.get("NAME", "") or ""),
        description=str(found.get("DESCRIPTION", "") or ""),
        version=str(found.get("VERSION", NO_VERSION) or NO_VERSION),
        api_version=api if isinstance(api, int) else 1,
    )


@dataclass(frozen=True)
class Supplied:
    """One of the plugins pycangui ships, before anybody has installed it."""

    name: str
    folder: Path
    info: Info

    @property
    def label(self) -> str:
        return self.info.title or self.name


def supplied() -> list[Supplied]:
    """The catalogue: what could be installed, in the order it is offered."""
    out = []
    folder = builtin_dir()
    if not folder.is_dir():
        return out
    for child in sorted(folder.iterdir()):
        entry = child / ENTRY
        if child.is_dir() and not child.name.startswith(("_", ".")) and entry.is_file():
            out.append(Supplied(child.name, child, describe(entry)))
    return out


@dataclass
class Loaded:
    """One plugin, and how it went."""

    name: str  # the folder name, which is what identifies it
    path: Path
    title: str = ""  # what it calls itself
    description: str = ""
    version: str = NO_VERSION
    error: str = ""
    app: Any = None
    #: Installed but switched off.  Listed, so that a plugin somebody turned
    #: off six months ago is findable rather than mysteriously absent, but
    #: none of its code has been run.
    active: bool = True

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def label(self) -> str:
        return self.title or self.name


@dataclass
class Plugins:
    """Everything installed, loaded and still loaded."""

    #: Where installed plugins live: one folder, in the workspace.  A plugin is
    #: code that gives a product's objects meaning, the same as a hook, so it
    #: travels with that product.
    folder: Path | None = None
    #: Installed and switched off, by folder name.  Held here rather than
    #: worked out from the folder, because "off" is a decision about the
    #: workspace and not a property of the files.
    disabled: set[str] = field(default_factory=set)
    make_app: Callable[[str], Any] | None = None
    log: Callable[[str], None] = print
    warn: Callable[[str], None] = print
    loaded: dict[str, Loaded] = field(default_factory=dict)

    # --- finding them --------------------------------------------------------------
    def found(self) -> dict[str, Path]:
        """Plugin folder name -> its entry file, whether or not it is switched on."""
        out: dict[str, Path] = {}
        if self.folder is None or not self.folder.is_dir():
            return out
        for child in sorted(self.folder.iterdir()):
            entry = child / ENTRY
            if child.is_dir() and not child.name.startswith(("_", ".")) and entry.is_file():
                out[child.name] = entry
        return out

    def is_active(self, name: str) -> bool:
        return name not in self.disabled

    # --- loading them ---------------------------------------------------------------
    def load_all(self) -> None:
        """Unload whatever is loaded, then load what is installed and switched on."""
        self.unload_all()
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # keep __pycache__ out of the user's folders
        try:
            for name, entry in self.found().items():
                if self.is_active(name):
                    self._load_one(name, entry)
                else:
                    self._note_inactive(name, entry)
        finally:
            sys.dont_write_bytecode = saved
        self._report()

    def _note_inactive(self, name: str, entry: Path) -> None:
        """List a switched-off plugin without running a line of it."""
        info = describe(entry)
        self.loaded[name] = Loaded(
            name=name,
            path=entry,
            title=info.title,
            description=info.description,
            version=info.version,
            active=False,
        )

    def _load_one(self, name: str, entry: Path) -> None:
        record = Loaded(name=name, path=entry)
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
        record.version = str(getattr(module, "VERSION", NO_VERSION) or NO_VERSION)

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
        """The ones that are actually adding something to the window."""
        return [r for r in self.loaded.values() if r.ok and r.active]

    def inactive(self) -> list[Loaded]:
        return [r for r in self.loaded.values() if not r.active]

    def _report(self) -> None:
        good = self.working()
        if good:
            self.log(f"Plugins loaded: {', '.join(sorted(r.label for r in good))}")
        for name, error in self.errors().items():
            self.warn(f"Plugin {name} failed to load:\n{error}")
