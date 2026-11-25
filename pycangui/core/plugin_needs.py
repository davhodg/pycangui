# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""What a plugin needs that pycangui does not bring: ``REQUIRES`` and ``lib/``.

Two ways for a plugin to use a package pycangui does not install:

* **``lib/``**, a folder in the plugin holding the package itself -- what
  ``pip install --target lib <package>`` leaves. Added to the import path
  while the plugin is loaded, so a pure-Python package travels in the zip and
  needs no pip at all, the Windows build included.
* **``REQUIRES``**, the packages it needs in pip's form (``["pyserial>=3.5"]``).
  One that is missing is named, rather than the plugin failing to import with
  an error that does not say what to install; where pip exists it is offered.

A process holds one copy of a package, so two plugins carrying different
versions of one in their ``lib/`` cannot both be loaded: the second is
refused with both versions named, rather than silently getting the first's.
"""

from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from importlib import metadata
from pathlib import Path

from pycangui.core.packages import Requirement

#: The folder in a plugin whose packages it brings with it.
LIB = "lib"
_DIST_INFO = re.compile(r"^(?P<name>.+?)-(?P<version>[^-]+)\.(?:dist|egg)-info$")


def missing(requires) -> list[str]:
    """The entries of ``requires`` that are not installed, or not in a version
    they allow -- each as written, with the version there is where there is one."""
    out = []
    for text in requires or ():
        try:
            name = Requirement(str(text)).name
        except ValueError:
            out.append(str(text))
            continue
        if not _applies(str(text)):
            continue  # for another platform or Python
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            out.append(str(text))
            continue
        if not _allows(str(text), have):
            out.append(f"{text} (this has {have})")
    return out


def _applies(text: str) -> bool:
    _spec, _, marker = text.partition(";")
    if not marker.strip():
        return True
    try:
        from packaging.markers import Marker
    except ImportError:  # no way to judge it here: taken as needed
        return True
    try:
        return Marker(marker.strip()).evaluate()
    except Exception:
        return True


def _allows(text: str, have: str) -> bool:
    spec = text.partition(";")[0]
    spec = re.sub(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?", "", spec).strip()
    if not spec:
        return True
    try:
        from packaging.specifiers import SpecifierSet
    except ImportError:  # present, and nothing here to compare versions with
        return True
    try:
        return SpecifierSet(spec).contains(have, prereleases=True)
    except Exception:
        return True


def contents(lib: Path) -> dict[str, str | None]:
    """What a ``lib/`` folder provides: each top-level importable name, and the
    version its distribution says (None where nothing says)."""
    versions: dict[str, str] = {}
    names: set[str] = set()
    for child in lib.iterdir():
        found = _DIST_INFO.match(child.name)
        if found and child.is_dir():
            tops = child / "top_level.txt"
            listed = tops.read_text(encoding="utf-8").split() if tops.is_file() else []
            for top in listed or [found["name"].replace("-", "_")]:
                versions[top] = found["version"]
        elif child.name.startswith((".", "_")) or child.name in ("bin", "__pycache__"):
            continue
        elif child.is_dir() or child.suffix in (".py", ".pyd", ".so"):
            names.add(child.name.split(".")[0])
    return {name: versions.get(name) for name in sorted(names)}


class Libs:
    """Whose ``lib/`` provides which package, while their plugins are loaded."""

    def __init__(self) -> None:
        #: top-level name -> its version, and the plugins whose lib/ has it
        self._owner: dict[str, tuple[str | None, set[str]]] = {}
        #: plugin -> its lib folder on sys.path
        self._paths: dict[str, str] = {}

    def add(self, plugin: str, folder: Path, warn) -> str | None:
        """Put a plugin's ``lib/`` on the import path; why not, if it cannot be."""
        lib = folder / LIB
        if not lib.is_dir():
            return None
        provided = contents(lib)
        for name, version in provided.items():
            other = self._owner.get(name)
            if other is None or other[1] <= {plugin}:
                continue
            if version is None or version != other[0]:
                them = ", ".join(sorted(other[1] - {plugin}))
                return (
                    f"its lib/ has {name} {version or '(no version given)'}, and "
                    f"plugin {them} has {name} {other[0] or '(no version given)'} loaded "
                    "already: one process holds one copy of a package, so they cannot "
                    "both be loaded"
                )
        for name, version in provided.items():
            if name in self._owner:
                self._owner[name][1].add(plugin)  # the same version: shared
                continue
            if (spec := importlib.util.find_spec(name)) is not None:
                where = getattr(spec, "origin", None) or "elsewhere"
                warn(f"its lib/{name} is not used: pycangui already has {name}, from {where}")
                continue
            self._owner[name] = (version, {plugin})
        sys.path.append(str(lib))
        self._paths[plugin] = str(lib)
        importlib.invalidate_caches()
        return None

    def drop(self, plugin: str) -> None:
        """Take a plugin's ``lib/`` off the path, and the packages only it brought."""
        path = self._paths.pop(plugin, None)
        if path is not None and path in sys.path:
            sys.path.remove(path)
        for name, (_version, plugins) in list(self._owner.items()):
            plugins.discard(plugin)
            if plugins:
                continue  # another plugin's lib/ has the same one
            del self._owner[name]
            for loaded in [m for m in sys.modules if m == name or m.startswith(f"{name}.")]:
                sys.modules.pop(loaded, None)
        importlib.invalidate_caches()
