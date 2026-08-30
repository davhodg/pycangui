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

User files never crash the application and are never overwritten.
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
class _UserModule:
    path: Path
    functions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    error: str | None = None


class Hooks:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self._user: dict[str, _UserModule] = {}
        self._failed: set[tuple[str, str]] = set()  # (module, name) already reported
        self.ensure_user_files()
        self.reload()

    # --- files -------------------------------------------------------------
    def ensure_user_files(self) -> list[Path]:
        """Copy any defaults file the user doesn't have yet.  Returns new paths."""
        created = []
        for module in registry():
            dest = self.ctx.hooks_dir / f"{module}.py"
            if not dest.exists():
                shutil.copy(_defaults_path(module), dest)
                created.append(dest)
        return created

    def update_stubs(self) -> dict[str, list[str]]:
        """Append hooks that exist in the defaults but not in the user file.

        Never touches existing user code.  Whatever the appended code needs
        comes with it: the imports, including ``from __future__ import
        annotations`` (from Python 3.14 annotations are evaluated lazily, but
        on 3.12 and 3.13 they are evaluated as the function is defined, so a
        pasted-in signature mentioning ``Path`` would break the whole file),
        and the module-level tables it reads.  A hook that arrived without its
        DID_NAMES would raise on every call and fall back to the built-in
        default, which is a poor way to find out.

        Returns {module: [added names]}.
        """
        added: dict[str, list[str]] = {}
        for module, specs in registry().items():
            dest = self.ctx.hooks_dir / f"{module}.py"
            if not dest.exists():
                shutil.copy(_defaults_path(module), dest)
                added[module] = list(specs)
                continue
            text = dest.read_text(encoding="utf-8")
            present = set(re.findall(r"^def\s+(\w+)\s*\(", text, re.MULTILINE))
            missing = [name for name in specs if name not in present]
            if not missing:
                continue
            text = _with_imports_for(module, text)
            chunks = ["\n\n# --- added by 'Update hook stubs' ---\n"]
            chunks += ["\n\n" + source for _name, source in _missing_constants(module, text)]
            chunks += ["\n\n@hook\n" + inspect.getsource(specs[n].default) for n in missing]
            dest.write_text(text.rstrip("\n") + "".join(chunks) + "\n", encoding="utf-8")
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

    def errors(self) -> dict[str, str]:
        return {m: um.error for m, um in self._user.items() if um.error}

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
    """The import statements at the top of a defaults module."""
    lines = []
    for line in _defaults_path(module).read_text(encoding="utf-8").splitlines():
        if line.startswith(("import ", "from ")):
            lines.append(line)
        elif line.startswith(("@hook", "def ", "class ")):
            break
    return lines


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
    # Imports go at the top, where a reader expects to find them.
    return "\n".join(header) + "\n\n" + text.lstrip("\n")


def _defaults_path(module: str) -> Path:
    return Path(str(resources.files(DEFAULTS_PACKAGE) / f"{module}.py"))
