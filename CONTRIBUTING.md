# Contributing to pycangui

Bug reports, fixes and features are all welcome. pycangui is looked after by one person in their
own time, so there is no promised response time, but every issue and pull request is read.

Security problems go through [SECURITY.md](SECURITY.md), not a public issue.

## Policy

**Open an issue before large changes, to allow for discussion.** Trivial fixes can go straight to
a pull request.

**One change per pull request**, with commit messages that are short and say what changed.

**Tests with every change.** A fix comes with a test that fails without it. The full suite and
the linter must pass.

**Anything that can disturb equipment asks first.** Joining a bus, transmitting, replaying,
writing to a device and enabling a drive all go through pycangui's confirmations. A new action of
that kind does the same.

**Dependencies stay few, and never GPL-only.** pycangui is Apache-2.0, and the Windows installer
bundles what it depends on, so a GPL-only package cannot be used. LGPL is fine as a separate,
replaceable package, as Qt and python-can are. A new dependency needs a reason in the pull
request, and goes in the *Built with* table in the README.

**No proprietary material.** No DBC, EDS, A2L or log files, CAN identifiers or code belonging to
a company or product. Samples and tests use files written for pycangui (see
`pycangui/resources`) or openly licensed ones.

**Licensing.** Contributions are made under the Apache License 2.0, as section 5 of the licence
says; the hook and simulated-node templates in `pycangui/hooks` and `pycangui/nodes` are MIT-0.
Every source file starts with its SPDX licence line, and `tests/test_headers.py` checks it. You
are responsible for what you submit, however it was written.

## Setting up

Working on pycangui needs the same setup as running it from the source folder, plus a few extra
Python packages. The launcher already installs pycangui from the source folder in editable mode,
so an edit takes effect the next time it starts; all development adds is the `[dev]` extra --
pytest, pytest-xdist, ruff, pdoc and the MDF reader. The launcher leaves those out because running
the application does not need them.

After the launcher's first run, add them to its `.venv`:

```
.venv\Scripts\python -m pip install -e .[dev]
```

or, if `uv` was found and created the `.venv` (it has no pip of its own):

```
uv pip install --python .venv\Scripts\python.exe -e .[dev]
```

Without the launcher, `python -m venv .venv` first and then the pip line. On Linux and macOS the
folder is `.venv/bin` rather than `.venv\Scripts`.

## Tests and style

```
.venv\Scripts\python -m pytest -n auto
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m ruff format .
```

The suite is a thousand Qt tests and splits cleanly across processes, so `-n auto` runs it in well
under a minute rather than six. Leave it off when running a single file: starting the workers
costs more than the file does.

ruff sets the formatting, with lines of up to 100 characters. In prose -- the manual, the README,
messages, comments and docstrings -- sentences are separated by one space. Comments say *why*;
the code already says what.

`python build/screenshots.py` regenerates `pycangui/help/main-window.png`, the screenshot in the
README. It opens pycangui on the demo device for about a minute, with temporary settings of its
own, so leave the window alone while it runs.

`python build/api_docs.py` writes API pages for people writing hooks, simulated nodes and plugins
to `dist/api-docs/`. Only that surface, not the whole package: the manual says how to extend
pycangui, and these pages are where to look up exactly what an object offers.

## Continuous integration

`.github/workflows/ci.yml` runs the tests and lint on every push: the latest Python on Windows and
Linux, and the oldest supported Python on Linux as well, since bugs that only appear on the floor
are real but rarely platform specific. The installer is built only for a release -- push a `v*`
tag, or start the workflow by hand from the Actions tab -- and `.github/workflows/macos.yml` runs
the tests on macOS for each release too.

## Building the Windows installer

Only needed to check the packaging itself: releases are built by CI from a tag.

`build.cmd` produces a self-contained Windows application, and an installer if a compiler for one
is present:

```
build.cmd            tests, notices, PyInstaller, checks, then setup.exe
build.cmd nosetup    stop after the checked application folder
```

It runs the tests, regenerates `THIRD-PARTY-NOTICES.txt` from the installed package metadata,
builds a **one-directory** bundle with PyInstaller, checks the result, and then wraps it with
**Inno Setup**. The result is `dist\pycangui\pycangui.exe` and `dist\pycangui-<version>-setup.exe`.

**LGPL components are bundled.** Qt (PySide6), python-can and asammdf are all LGPL-3.0 and all
ship inside the installer. The LGPL asks that they stay *replaceable*: hence the one-directory
build, where each is a separate DLL or package a user can substitute their own build of, rather
than a single file. Their licences are reproduced in `THIRD-PARTY-NOTICES.txt`, generated from
installed package metadata so it cannot drift from what was actually shipped.

**GPL-only components are intentionally excluded.** PySide6 ships Qt Charts, Qt Data
Visualization, Qt Graphs and the Virtual Keyboard in the same wheel as the LGPL modules. They are
excluded in `pycangui.spec`, and `build/check_build.py` fails the build if one appears anyway.

`build/check_build.py` also fails the build if a sample or hook template is missing, or if the
built executable cannot import its protocol stacks, the MDF reader and every python-can adapter
backend -- it runs `pycangui.exe --selftest` to find out rather than guessing from file names.

## Versions and releases

The version is written once, as `__version__` in `pycangui/__init__.py`. A tag's installer is named
after the tag, and the build stops if the tag and `__version__` disagree. Any other build is named
`<version>-dev-<commit>`, so an installer that is not a release says so.

The same tag publishes the package to [PyPI](https://pypi.org/project/pycangui/), through
trusted publishing -- no API token is stored anywhere. Running the workflow by hand from the
Actions tab publishes to [TestPyPI](https://test.pypi.org/project/pycangui/) instead, as
`<version>.dev<run number>`, to check the page and the install before a release:

```
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ pycangui
```

The page on PyPI is `PYPI.md`, not the README. A version can never be uploaded twice, to either
index.

Publishing waits for everything: the tests on Windows, Linux and macOS, and the installer build.
A reviewer can be required on the `pypi` environment (repository *Settings > Environments*), so
that a release also waits for somebody to approve it.

**Making a release:**

1. Bump `__version__` in `pycangui/__init__.py`, commit and push.
2. Run the CI workflow by hand from the Actions tab. That is every check a release gets -- all
   the tests, macOS included, the installer build -- plus an upload to TestPyPI.
3. Look at the TestPyPI page, and install from it into a clean environment.
4. Tag the same commit, `git tag v<version>`, and push the tag. The same checks run again, then
   the installer goes to the GitHub release and the package to PyPI.

A tag whose run fails publishes nothing: delete it, fix, and tag again.
