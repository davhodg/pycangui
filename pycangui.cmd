@echo off
rem Launch pycangui.  Creates the virtual environment on first run (needs
rem python 3.12+ on PATH), then starts the application.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e ".[dev,xcp]" || goto :fail
)
start "" ".venv\Scripts\pythonw.exe" -m pycangui %*
exit /b 0
:fail
echo.
echo Setup failed. Is Python 3.12 or newer on the PATH?
pause
exit /b 1
