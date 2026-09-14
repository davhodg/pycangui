# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""User-modifiable hooks.

A *hook* is a plain function the application calls at a decision point.  The
built-in defaults live in ``pycangui/hooks/<module>.py``, each marked with
``@hook``.  On first run every defaults file is copied verbatim into the user's
``hooks/`` folder, so the user starts from working, commented code.

Call order for ``hooks.call("canopen", "eds_for_node", identity)``:

1. the user's ``hooks/canopen.py::eds_for_node`` if the file loaded and defines it;
2. if it raises -> traceback goes to the log, fall through;
3. if it returns ``None`` -> "do the normal thing", fall through;
4. the built-in default.

User files never crash the application, and a file the user has changed is
never overwritten (see core.supplied for how the untouched ones keep up).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import shutil
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from pycangui.core.context import Context
from pycangui.core.supplied import Supplied

DEFAULTS_PACKAGE = "pycangui.hooks"


@dataclass
class HookSpec:
    module: str
    name: str
    default: Callable[..., Any]

    @property
    def doc(self) -> str:
        return inspect.getdoc(self.default) or ""


# module name -> hook name -> spec.  Filled by @hook at import of the defaults.
_REGISTRY: dict[str, dict[str, HookSpec]] = {}


def hook(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a function in ``pycangui/hooks/<module>.py`` as a hook default.

    In a *user* copy of the file the decorator is a harmless no-op, so the
    user file can keep it for documentation.
    """
    pkg, _, module = fn.__module__.rpartition(".")
    if pkg == DEFAULTS_PACKAGE:
        _REGISTRY.setdefault(module, {})[fn.__name__] = HookSpec(module, fn.__name__, fn)
    return fn


def _load_defaults() -> None:
    """Import every module in pycangui/hooks so the registry is populated."""
    for entry in resources.files(DEFAULTS_PACKAGE).iterdir():
        if entry.name.endswith(".py") and not entry.name.startswith("_"):
            importlib.import_module(f"{DEFAULTS_PACKAGE}.{entry.name[:-3]}")


def registry() -> dict[str, dict[str, HookSpec]]:
    if not _REGISTRY:
        _load_defaults()
    return _REGISTRY


@dataclass
class _Mismatch:
    """A hook the user has written that pycangui cannot call."""

    signature: str  #: what they wrote
    why: str  #: how it differs from what is called


@dataclass
class _UserModule:
    path: Path
    functions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    error: str | None = None
    #: name -> what is wrong with it.  Kept out of ``functions`` so that the
    #: default runs instead of the call failing at the worst moment.
    mismatched: dict[str, _Mismatch] = field(default_factory=dict)


class Hooks:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._user: dict[str, _UserModule] = {}
        self._failed: set[tuple[str, str]] = set()  # (module, name) already reported
        #: (module, name) whose signature this build changed, from sync().
        self._changed: list[tuple[str, str]] = []
        #: The hook files as supplied: what was copied, and what ships now.
        self.supplied = Supplied(
            "hooks",
            ctx.hooks_dir,
            {f"{module}.py": _defaults_path(module) for module in registry()},
            ctx.settings,
        )
        self.ensure_user_files()
        self.sync()
        self.reload()
        self.report()

    # --- files -------------------------------------------------------------
    def ensure_user_files(self) -> list[Path]:
        """Copy the hook files the user lacks, and bring the untouched ones up
        to this version.  An edited one is left alone and, once per newer
        version, mentioned.  Returns the paths newly copied."""
        outcome = self.supplied.update(self.ctx.log)
        return [self.ctx.hooks_dir / name for name in outcome.copied]

    def edited(self) -> list[str]:
        """Which hook modules differ from the ones pycangui ships."""
        return [name.removesuffix(".py") for name in self.supplied.edited()]

    def restore(self, module: str) -> Path:
        """Put pycangui's version of a hook file back, keeping the old one.

        An edited hook is the usual way to break things, so there has to be a
        way back -- but the file is code somebody wrote and may be the only
        copy of it, so this renames rather than deletes.  Returns where the
        old one went.
        """
        return self.supplied.restore(f"{module}.py")

    def sync(self) -> dict[str, list[str]]:
        """Bring the hook files up to date with this build, at startup.

        Keeping up with a new version of pycangui is pycangui's job, not a
        menu entry the user has to know about and remember to use after every
        upgrade.  Nothing here rewrites a line the user wrote: the only change
        made to a file is appending hooks that did not exist when it was last
        looked at.

        What "last looked at" means is the signature of every hook, recorded
        in the workspace settings.  A recorded list rather than a version
        number because the version is no help to somebody running from a
        checkout, and because it is what says a hook is *new* rather than
        deleted on purpose -- a hook the user took out of their file stays
        out, instead of coming back every startup.

        Returns {module: [added names]}, and leaves the hooks whose signature
        changed since last time in ``_changed`` for :meth:`report`.
        """
        current = {
            module: {name: _signature(spec.default) for name, spec in specs.items()}
            for module, specs in registry().items()
        }
        known = self.ctx.settings.get("hooks.known") or {}
        if known:
            only = {
                module: [name for name in specs if name not in known.get(module, {})]
                for module, specs in current.items()
            }
            self._changed = [
                (module, name)
                for module, specs in current.items()
                for name, signature in specs.items()
                if signature != known.get(module, {}).get(name, signature)
            ]
        else:
            # No record at all: either a fresh install, where the files have
            # just been copied whole and there is nothing to add, or an
            # upgrade from before this was written, where there may be plenty.
            only = None
            self._changed = []
        added = self.update_stubs(only=only)
        self.ctx.settings.set("hooks.known", current)
        return added

    def report(self) -> None:
        """Say what sync() and reload() found, in the order it matters."""
        for module, um in self._user.items():
            for name, bad in um.mismatched.items():
                because = (
                    "this version of pycangui changed it"
                    if (module, name) in self._changed
                    else "it does not match the hook pycangui calls"
                )
                self.ctx.warn(
                    f"hooks/{module}.py: {name} is not being used, because "
                    f"{because} -- {bad.why}.\n"
                    f"    yours:    {bad.signature}\n"
                    f"    pycangui: {_signature(registry()[module][name].default)}\n"
                    "    The built-in default is running instead.  See Hooks in the manual "
                    "(Help > Documentation)."
                )

    def update_stubs(self, only: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
        """Append hooks that exist in the defaults but not in the user file.

        Never touches existing user code.  Whatever the appended code needs
        comes with it: the imports, including ``from __future__ import
        annotations`` (from Python 3.14 annotations are evaluated lazily, but
        on 3.12 and 3.13 they are evaluated as the function is defined, so a
        pasted-in signature mentioning ``Path`` would break the whole file),
        and the module-level tables it reads.  A hook that arrived without its
        DID_NAMES would raise on every call and fall back to the built-in
        default, which is a poor way to find out.

        With ``only``, consider just those names per module; without it, every
        hook the file is missing.  Returns {module: [added names]}.
        """
        added: dict[str, list[str]] = {}
        for module, specs in registry().items():
            wanted = list(specs) if only is None else only.get(module, [])
            if not wanted:
                continue
            dest = self.ctx.hooks_dir / f"{module}.py"
            if not dest.exists():
                shutil.copy(_defaults_path(module), dest)
                added[module] = list(specs)
                continue
            text = dest.read_text(encoding="utf-8")
            present = set(re.findall(r"^def\s+(\w+)\s*\(", text, re.MULTILINE))
            missing = [name for name in wanted if name not in present]
            if not missing:
                continue
            grown = _with_imports_for(module, text)
            chunks = ["\n\n# --- hooks pycangui added; yours to edit ---\n"]
            chunks += ["\n\n" + source for _name, source in _missing_constants(module, grown)]
            chunks += ["\n\n@hook\n" + inspect.getsource(specs[n].default) for n in missing]
            dest.write_text(grown.rstrip("\n") + "".join(chunks) + "\n", encoding="utf-8")
            # A working file that stops working is a worse outcome than a
            # missing stub, and this runs unattended at startup -- so put the
            # old one back if what we appended does not import.
            if (broke := _fails_to_load(module, dest)) is not None:
                dest.write_text(text, encoding="utf-8")
                self.ctx.warn(
                    f"hooks/{module}.py left as it was: adding "
                    f"{', '.join(missing)} would have broken it:\n{broke}"
                )
                continue
            added[module] = missing
        return added

    # --- loading -----------------------------------------------------------
    def reload(self) -> None:
        self._user.clear()
        self._failed.clear()
        saved = sys.dont_write_bytecode
        sys.dont_write_bytecode = True  # keep __pycache__ out of the user's hooks folder
        try:
            self._load_all()
        finally:
            sys.dont_write_bytecode = saved

    def _load_all(self) -> None:
        for module in registry():
            path = self.ctx.hooks_dir / f"{module}.py"
            um = _UserModule(path)
            self._user[module] = um
            if not path.exists():
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"pycangui_user_hooks.{module}", path)
                assert spec is not None and spec.loader is not None
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception:
                um.error = traceback.format_exc()
                self.ctx.warn(f"Hook file {path} failed to load, using defaults:\n{um.error}")
                continue
            um.functions = {
                name: fn
                for name, fn in vars(mod).items()
                if callable(fn)
                and name in registry()[module]
                and getattr(fn, "__module__", "") == mod.__name__
            }
            # Checked here rather than discovered at the call, because the
            # call may be hours away and in the middle of something: a hook
            # written against an older pycangui should be a line in the log
            # at startup, not a flash programming session that quietly used
            # the default seed-to-key.
            for name, fn in list(um.functions.items()):
                why = _why_uncallable(fn, registry()[module][name].default)
                if why is not None:
                    um.mismatched[name] = _Mismatch(_signature(fn), why)
                    del um.functions[name]

    def errors(self) -> dict[str, str]:
        return {m: um.error for m, um in self._user.items() if um.error}

    def mismatches(self) -> dict[tuple[str, str], str]:
        """(module, name) -> why that hook is not being called."""
        return {
            (module, name): bad.why
            for module, um in self._user.items()
            for name, bad in um.mismatched.items()
        }

    def is_user_defined(self, module: str, name: str) -> bool:
        return name in self._user.get(module, _UserModule(Path())).functions

    # --- calling -----------------------------------------------------------
    def call(self, module: str, name: str, *args: Any, **kwargs: Any) -> Any:
        spec = registry()[module][name]
        user_fn = self._user[module].functions.get(name)
        if user_fn is not None:
            try:
                result = user_fn(*args, ctx=self.ctx, **kwargs)
            except Exception:
                if (module, name) not in self._failed:
                    self._failed.add((module, name))
                    self.ctx.log(
                        f"Hook {module}.{name} raised, using default "
                        f"(reported once until reload):\n{traceback.format_exc()}"
                    )
            else:
                if result is not None:
                    return result
        return spec.default(*args, ctx=self.ctx, **kwargs)


def _signature(fn: Callable[..., Any]) -> str:
    """``name(a, b, *, ctx)`` -- no annotations, no defaults.

    Annotations are noise when the point is which arguments there are, and
    they are written differently in different files anyway (``bytes`` against
    ``bytes | None``, a string against a name) without the call changing.
    """
    sig = inspect.signature(fn)
    bare = sig.replace(
        parameters=[
            p.replace(annotation=p.empty, default=p.empty) for p in sig.parameters.values()
        ],
        return_annotation=inspect.Signature.empty,
    )
    return f"{fn.__name__}{bare}"


def _why_uncallable(fn: Callable[..., Any], default: Callable[..., Any]) -> str | None:
    """How ``fn`` fails to accept the call ``default`` is written for.

    ``call()`` passes the plain parameters positionally and the keyword-only
    ones -- ``ctx``, always -- by name, so that is the shape to check against.
    None means it can be called; anything else is a sentence for the log.
    """
    wanted = inspect.signature(default).parameters.values()
    count = sum(1 for p in wanted if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))
    by_name = {p.name for p in wanted if p.kind is p.KEYWORD_ONLY}

    params = list(inspect.signature(fn).parameters.values())
    if any(p.kind is p.VAR_POSITIONAL for p in params) and any(
        p.kind is p.VAR_KEYWORD for p in params
    ):
        return None  # *args, **kwargs takes whatever it is given
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    needed = sum(1 for p in positional if p.default is p.empty)
    if not any(p.kind is p.VAR_POSITIONAL for p in params):
        if len(positional) < count:
            return f"it takes {len(positional)} argument(s) where pycangui passes {count}"
        if needed > count:
            return f"it needs {needed} argument(s) where pycangui passes {count}"
    open_names = {p.name for p in params if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)} - {
        p.name for p in positional[:count]
    }
    if not any(p.kind is p.VAR_KEYWORD for p in params):
        if absent := sorted(by_name - open_names):
            return f"it does not take {', '.join(absent)}"
    unmet = sorted(
        p.name
        for p in params
        if p.kind is p.KEYWORD_ONLY and p.default is p.empty and p.name not in by_name
    )
    if unmet:
        return f"it requires {', '.join(unmet)}, which pycangui does not pass"
    return None


def _fails_to_load(module: str, path: Path) -> str | None:
    """Import a hook file for the sake of finding out whether it imports."""
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(f"pycangui_hook_check.{module}", path)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    except Exception:
        return traceback.format_exc()
    finally:
        sys.dont_write_bytecode = saved
    return None


def _module_constants(module: str) -> list[tuple[str, str]]:
    """(name, source) for every module-level assignment in a defaults file.

    These are the tables the hooks read -- DID_NAMES, SPN_NAMES -- and are as
    much a part of a hook as its body is.
    """
    source = _defaults_path(module).read_text(encoding="utf-8")
    found = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            if not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
        else:
            continue
        if (text := ast.get_source_segment(source, node)) is not None:
            found.append((name, text))
    return found


def _missing_constants(module: str, text: str) -> list[tuple[str, str]]:
    """The defaults' tables that the user's file does not already define."""
    return [
        (name, source)
        for name, source in _module_constants(module)
        if not re.search(rf"^{re.escape(name)}\s*[:=]", text, re.MULTILINE)
    ]


def _import_lines(module: str) -> list[str]:
    """The import statements of a defaults module, one statement per line.

    Parsed rather than read off the top of the file.  Reading lines took the
    first line of a parenthesised import and left the rest behind, which put
    "from pycangui.uds.standard import (" into somebody's hook file and broke
    every hook in it.
    """
    tree = ast.parse(_defaults_path(module).read_text(encoding="utf-8"))
    return [
        ast.unparse(node) for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]


def _with_imports_for(module: str, text: str) -> str:
    """Add whatever imports the appended hook sources will need."""
    future = "from __future__ import annotations"
    header = [line for line in _import_lines(module) if line.split("#")[0].strip() not in text]
    if future in text and future in header:
        header.remove(future)
    elif future not in text and future not in header:
        header.insert(0, future)
    if not header:
        return text
    # Imports go at the top, where a reader expects to find them -- but under
    # the licence header and the docstring, not above them.  Above demoted the
    # docstring to a stray string and pushed the SPDX line down the file,
    # where licence scanners do not look for it.
    at = _top_of(text)
    before = text[:at]
    if before and not before.endswith("\n"):
        before += "\n"
    return before + ("\n" if before else "") + "\n".join(header) + "\n\n" + text[at:].lstrip("\n")


def _top_of(text: str) -> int:
    """Where imports belong: after a leading comment block, any docstring, and
    any ``from __future__`` import.

    The last of those is not a matter of taste.  Python accepts a future import
    only as a file's first statement, so a line put above one breaks the whole
    file -- which is what happened to an older uds.py, docstring then its own
    future import, the first time it was given hooks that needed new imports.

    A file that does not parse -- somebody's half-finished edit -- still has
    a comment block worth stepping over, so that is found line by line.
    """
    lines = text.splitlines(keepends=True)
    try:
        body = ast.parse(text).body
    except SyntaxError:
        body = []
    end = 0
    rest = iter(body)
    node = next(rest, None)
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.end_lineno is not None
    ):
        end = node.end_lineno
        node = next(rest, None)
    while isinstance(node, ast.ImportFrom) and node.module == "__future__":
        end = node.end_lineno or end
        node = next(rest, None)
    if end:
        return sum(len(line) for line in lines[:end])
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "from __future__ import")):
            break
        offset += len(line)
    return offset


def _defaults_path(module: str) -> Path:
    return Path(str(resources.files(DEFAULTS_PACKAGE) / f"{module}.py"))
