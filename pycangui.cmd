@echo off
rem Launch pycangui. Sets itself up on first run (needs Python 3.12 or newer on
rem the PATH), then starts the application.
rem
rem The setup deliberately is not quiet: it downloads a couple of hundred
rem megabytes, mostly Qt, and a silent several-minute pause looks like a hang.
rem If "uv" is installed it is used instead of pip. The first run is limited by
rem the download whichever is used, but uv caches packages, so later rebuilds of
rem .venv take seconds.
rem
rem Batch files must keep CRLF line endings, or cmd.exe mis-parses labels and
rem goto; .gitattributes enforces that.
setlocal
cd /d "%~dp0"

rem Stamped before anything else so that --timing can say what this file
rem cost before Python was reached: the dependency check is a whole Python
rem start on its own, and none of it is visible from inside the application.
set PYCANGUI_LAUNCH_AT=%TIME%

if exist ".venv\Scripts\python.exe" goto :check

echo.
echo ============================================================
echo  First run: setting up pycangui
echo ============================================================
echo.
echo  pycangui is written in Python, and needs a set of libraries
echo  to run. Rather than install those into the Python on your
echo  machine -- where they could clash with something else --
echo  they go into a "virtual environment": a self contained
echo  folder called .venv, right next to this script, holding its
echo  own copy of Python and only the libraries pycangui needs.
echo.
echo  Nothing outside that folder is touched, and deleting .venv
echo  undoes the whole thing.
echo.
echo  This runs once. It downloads roughly 250 MB, most of it Qt
echo  (the toolkit the windows are drawn with), so expect a few
echo  minutes. Every later start is immediate.
echo.

echo  [1/3] Checking Python...
python --version
if errorlevel 1 goto :nopython

rem uv is a much faster drop-in replacement for pip. Used if it happens to be
rem installed; never required.
set UV=
where uv >nul 2>&1 && set UV=1
if defined UV echo        uv found: using it instead of pip.

echo.
echo  [2/3] Creating the virtual environment in .venv ...
if defined UV (
    uv venv .venv
) else (
    python -m venv .venv
    if errorlevel 1 goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
)
if errorlevel 1 goto :fail

echo.
echo  [3/3] Installing pycangui and the libraries it runs on.
echo        Not the test tools -- see the README if you want those.
echo        Each package is listed as it downloads; the big one is
echo        PySide6, which is Qt.
echo.
if defined UV (
    uv pip install --python ".venv\Scripts\python.exe" -e "."
) else (
    ".venv\Scripts\python.exe" -m pip install -e "."
)
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo  Setup finished. Starting pycangui...
echo ============================================================
if not defined UV (
    echo.
    echo  Tip: "pip install uv" once, and this setup takes seconds
    echo  rather than minutes the next time you do it.
)
echo.


rem A .venv built before a dependency was added is short of it, and the
rem launcher below runs pythonw, which has no console for the ImportError
rem to appear in: the window would simply never open. So ask -- but only
rem when the answer could have changed.
rem
rem The check costs a whole Python start, a quarter of a second on every
rem launch, to answer a question whose answer only changes when pyproject.toml
rem does. So the answer is kept as a copy of the file it was the answer to,
rem and fc compares the two: identical means asked and answered already.
:check
fc /b "pyproject.toml" ".venv\.deps-ok" >nul 2>&1
if not errorlevel 1 goto :run
".venv\Scripts\python.exe" "build\check_deps.py" >nul 2>&1
if not errorlevel 1 goto :stamp
echo.
echo  pycangui needs libraries that this folder does not have yet.
echo  Installing them; this is much quicker than the first setup was.
echo.
set UV=
where uv >nul 2>&1 && set UV=1
if defined UV (
    uv pip install --python ".venv\Scripts\python.exe" -e "."
) else (
    ".venv\Scripts\python.exe" -m pip install -e "."
)
if errorlevel 1 goto :fail

rem Written only after a successful check or install, so a failed one is asked
rem again next time rather than remembered as an answer.
:stamp
copy /y "pyproject.toml" ".venv\.deps-ok" >nul

:run
start "" ".venv\Scripts\pythonw.exe" -m pycangui %*
exit /b 0

:nopython
echo.
echo Python was not found on the PATH.
echo Install Python 3.12 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer.
pause
exit /b 1

:fail
echo.
echo Setup failed ^(errorlevel %errorlevel%^). The messages above say why;
echo the usual causes are no internet connection, a proxy that blocks
echo pypi.org, or a Python older than 3.12.
pause
exit /b 1
