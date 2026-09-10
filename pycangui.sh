#!/bin/sh
# Launch pycangui on Linux or macOS.  Sets itself up on first run (needs
# python3 3.12 or newer on the PATH), then starts the application.
#
# The setup deliberately is not quiet: it downloads a couple of hundred
# megabytes, mostly Qt, and a silent several-minute pause looks like a hang.
cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    cat <<'BANNER'

============================================================
 First run: setting up pycangui
============================================================
 pycangui is written in Python, and needs a set of libraries
 to run.  Rather than install those into the Python on your
 machine -- where they could clash with something else -- they
 go into a "virtual environment": a self contained folder
 called .venv, right next to this script, holding its own copy
 of Python and only the libraries pycangui needs.

 Nothing outside that folder is touched, and deleting .venv
 undoes the whole thing.

 This runs once.  It downloads roughly 90 MB, most of it Qt
 (the toolkit the windows are drawn with), so on a slow
 connection expect a few minutes.  Every later start is
 immediate.

BANNER
    echo " [1/3] Checking Python..."
    python3 --version || {
        echo
        echo "python3 was not found.  Install Python 3.12 or newer."
        exit 1
    }

    echo
    # uv is a much faster drop-in replacement for pip.  Used if it happens to
    # be installed; never required.
    if command -v uv >/dev/null 2>&1; then
        UV=1
        echo "       uv found: using it instead of pip."
    else
        UV=
    fi

    echo
    echo " [2/3] Creating the virtual environment in .venv ..."
    if [ -n "$UV" ]; then
        uv venv .venv || exit 1
    else
        python3 -m venv .venv || exit 1
        .venv/bin/python -m pip install --upgrade pip || exit 1
    fi

    echo
    echo " [3/3] Installing pycangui and its libraries."
    echo "       Each package is listed as it downloads; the big one is"
    echo "       PySide6, which is Qt."
    echo
    { if [ -n "$UV" ]; then
        uv pip install --python .venv/bin/python -e "."
      else
        .venv/bin/python -m pip install -e "."
      fi } || {
        echo
        echo "Setup failed.  The messages above say why; the usual causes are"
        echo "no internet connection, a proxy that blocks pypi.org, a Python"
        echo "older than 3.12, or missing Qt system libraries."
        exit 1
    }

    cat <<'DONE'

============================================================
 Setup finished.  Starting pycangui...
============================================================

 Tip: "pip install uv" once.  The first setup is limited by the
 download either way, but uv caches the packages, so rebuilding
 this folder later takes seconds not minutes.

DONE
elif cmp -s pyproject.toml .venv/.deps-ok; then
    # Already checked against exactly this pyproject.toml.  The check costs a
    # whole Python start -- a quarter of a second on every launch, for a
    # question whose answer only changes when this file does -- so the answer
    # is kept, as a copy of the file it was the answer to.
    :
elif ! .venv/bin/python build/check_deps.py >/dev/null 2>&1; then
    # A .venv built before a dependency was added is short of it, and nothing
    # would say so beyond an ImportError on the way up.  Asked whenever
    # pyproject.toml has changed since the last time it was asked.
    echo
    echo "pycangui needs libraries that this folder does not have yet."
    echo "Installing them; this is much quicker than the first setup was."
    echo
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python .venv/bin/python -e "." || exit 1
    else
        .venv/bin/python -m pip install -e "." || exit 1
    fi
    cp pyproject.toml .venv/.deps-ok
else
    cp pyproject.toml .venv/.deps-ok
fi

exec .venv/bin/python -m pycangui "$@"
