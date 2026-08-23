@echo off
rem Build a distributable pycangui: a PyInstaller one-directory build, checked,
rem then wrapped in a Windows installer.
rem
rem   build.cmd            build, check, and make setup.exe if an installer
rem                        compiler (Inno Setup or NSIS) is available
rem   build.cmd nosetup    stop after the checked one-directory build
rem
rem Two notes for editing this file: batch files must keep CRLF line endings or
rem cmd.exe mis-parses labels and goto (.gitattributes enforces it), and
rem "if errorlevel 1 goto :fail" is used rather than "|| goto :fail", which is
rem unreliable after a quoted interpreter invocation.
setlocal
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
if not exist "%PYTHON%" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto :fail
    "%PYTHON%" -m pip install --quiet --upgrade pip
)
"%PYTHON%" -m pip install --quiet -e ".[dev]"
if errorlevel 1 goto :fail
"%PYTHON%" -m pip install --quiet pyinstaller
if errorlevel 1 goto :fail

echo.
echo === Tests =========================================================
"%PYTHON%" -m pytest -q tests
if errorlevel 1 goto :fail

echo.
echo === Third party notices ===========================================
"%PYTHON%" build\notices.py
if errorlevel 1 goto :fail

echo.
echo === PyInstaller ===================================================
rmdir /s /q dist\pycangui 2>nul
"%PYTHON%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build\work build\pycangui.spec
if errorlevel 1 goto :fail

echo.
echo === Checks ========================================================
"%PYTHON%" build\check_build.py
if errorlevel 1 goto :fail

if /i "%~1"=="nosetup" goto :done

for /f %%v in ('"%PYTHON%" -c "import pycangui; print(pycangui.__version__)"') do set VERSION=%%v

echo.
echo === Installer =====================================================
set ISCC=
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%p set ISCC=%%p
set MAKENSIS=
for %%p in (
    "%ProgramFiles(x86)%\NSIS\makensis.exe"
    "%ProgramFiles%\NSIS\makensis.exe"
) do if exist %%p set MAKENSIS=%%p

if defined ISCC (
    echo Using Inno Setup.
    %ISCC% /DAppVersion=%VERSION% build\installer.iss
    if errorlevel 1 goto :fail
    echo Installer: dist\pycangui-%VERSION%-setup.exe
    goto :done
)
if defined MAKENSIS (
    echo Using NSIS.
    %MAKENSIS% /DAppVersion=%VERSION% build\installer.nsi
    if errorlevel 1 goto :fail
    echo Installer: dist\pycangui-%VERSION%-setup.exe
    goto :done
)
echo No installer compiler found, so no setup.exe was made.
echo The application is ready in dist\pycangui.
echo Install Inno Setup ^(https://jrsoftware.org/isinfo.php^)
echo or NSIS ^(https://nsis.sourceforge.io^) to build one.

:done
echo.
echo Application: dist\pycangui\pycangui.exe
exit /b 0

:fail
echo.
echo BUILD FAILED ^(errorlevel %errorlevel%^)
exit /b 1
