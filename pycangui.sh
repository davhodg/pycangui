#!/bin/sh
# Launch pycangui on Linux / macOS.  Creates the virtual environment on first
# run (needs python3 3.12+ on PATH), then starts the application.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv || exit 1
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -e ".[dev,xcp]" || exit 1
fi
exec .venv/bin/python -m pycangui "$@"
