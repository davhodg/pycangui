#!/bin/sh
# Launch pycangui on Linux or macOS. Sets itself up on first run (needs
# python3 3.12 or newer on the PATH), then starts the application.
#
# The setup deliberately is not quiet: it downloads a couple of hundred
# megabytes, mostly Qt, and a silent several-minute pause looks like a hang.

# Stamped first, so --timing can say what this script cost before Python was
# reached; the dependency check below is a Python start of its own.
export PYCANGUI_LAUNCH_AT=$(date +%s.%N 2>/dev/null || date +%s)

# Say why and stop. Started from a file manager, the terminal window closes
# as soon as this script does and takes the message with it, so it waits
# for Enter -- as pycangui.cmd pauses on Windows. With no terminal at all, a
# dialog if there is something to show one.
fail() {
    echo
    printf '%s\n' "$@"
    if [ -t 0 ]; then
        echo
        printf 'Press Enter to close. '
        read -r _
    elif command -v zenity >/dev/null 2>&1; then
        zenity --error --title=pycangui --text="$(printf '%s\n' "$@")" 2>/dev/null
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send pycangui "$(printf '%s\n' "$@")"
    fi
    exit 1
}

cd "$(dirname "$0")" || exit 1

if [ ! -x .venv/bin/python ]; then
    cat <<'BANNER'

============================================================
 First run: setting up pycangui
============================================================
 pycangui is written in Python, and needs a set of libraries
 to run. Rather than install those into the Python on your
 machine -- where they could clash with something else -- they
 go into a "virtual environment": a self contained folder
 called .venv, right next to this script, holding its own copy
 of Python and only the libraries pycangui needs.

 Nothing outside that folder is touched, and deleting .venv
 undoes the whole thing.

 This runs once. It downloads a couple of hundred megabytes, most of it Qt
 (the toolkit the windows are drawn with), so on a slow
 connection expect a few minutes. Every later start is
 immediate.

BANNER
    echo " [1/3] Checking Python..."
    python3 --version || fail "python3 was not found. Install Python 3.12 or newer."
    python3 -c "import sys; sys.exit(sys.version_info < (3, 12))" ||
        fail "pycangui needs Python 3.12 or newer, and this python3 is older."

    echo
    # uv is a much faster drop-in replacement for pip. Used if it happens to
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
        uv venv .venv || fail "Creating .venv failed. The messages above say why."
    else
        # Debian, Ubuntu and Linux Mint ship the venv module without what it
        # needs to install pip, as a separate package. Asked before creating
        # anything: a failed attempt leaves a .venv with a python in it,
        # which the next run would take as finished.
        python3 -c "import ensurepip, venv" 2>/dev/null ||
            fail "Python's venv module is not complete here. On Debian, Ubuntu and" \
                "Linux Mint it is a separate package:" "" \
                "    sudo apt install python3-venv" "" \
                "then run ./pycangui.sh again."
        python3 -m venv .venv ||
            { rm -rf .venv; fail "Creating .venv failed. The messages above say why."; }
        .venv/bin/python -m pip install --upgrade pip ||
            { rm -rf .venv; fail "Updating pip failed. The messages above say why."; }
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
      fi } || fail "Setup failed. The messages above say why; the usual causes are" \
        "no internet connection, or a proxy that blocks pypi.org."

    # The entry starts this script rather than .venv's Python, so a start
    # from the menu still notices libraries added by a later pull. Asked
    # only with somebody at the terminal to answer.
    if [ -t 0 ] && [ "$(uname -s)" = Linux ]; then
        echo
        printf ' Add pycangui to the applications menu? [y/N] '
        read -r answer
        case $answer in
            [Yy]*) .venv/bin/python -m pycangui.core.shortcut ;;
        esac
    fi

    cat <<'DONE'

============================================================
 Setup finished. Starting pycangui...
============================================================

 Tip: "pip install uv" once. The first setup is limited by the
 download either way, but uv caches the packages, so rebuilding
 this folder later takes seconds not minutes.

DONE
elif cmp -s pyproject.toml .venv/.deps-ok; then
    # Already checked against exactly this pyproject.toml. The check costs a
    # whole Python start -- a quarter of a second on every launch, for a
    # question whose answer only changes when this file does -- so the answer
    # is kept, as a copy of the file it was the answer to.
    :
elif ! .venv/bin/python build/check_deps.py >/dev/null 2>&1; then
    # A .venv built before a dependency was added is short of it, and nothing
    # would say so beyond an ImportError on the way up. Asked whenever
    # pyproject.toml has changed since the last time it was asked.
    echo
    echo "pycangui needs libraries that this folder does not have yet."
    echo "Installing them; this is much quicker than the first setup was."
    echo
    if command -v uv >/dev/null 2>&1; then
        uv pip install --python .venv/bin/python -e "." ||
            fail "Installing the new libraries failed. The messages above say why."
    else
        .venv/bin/python -m pip install -e "." ||
            fail "Installing the new libraries failed. The messages above say why."
    fi
    cp pyproject.toml .venv/.deps-ok
else
    cp pyproject.toml .venv/.deps-ok
fi

# Stamped again, so --timing can tell this script's own work apart from
# starting the interpreter: the two have different cures.
export PYCANGUI_PYTHON_AT=$(date +%s.%N 2>/dev/null || date +%s)
# Not exec: a pycangui that stops with an error -- a library missing, a
# crash on the way up -- would close a file manager's terminal before the
# message could be read. A normal exit just ends.
.venv/bin/python -m pycangui "$@" ||
    fail "pycangui stopped with an error. The messages above say why."
