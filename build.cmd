@echo off
rem Build a distributable pycangui: a PyInstaller one-directory build, checked,
rem then wrapped in a Windows installer with Inno Setup.
rem
rem   build.cmd            build, check, and make setup.exe
rem   build.cmd nosetup    stop after the checked one-directory build
rem   build.cmd setup      only make setup.exe, from the dist\pycangui already
rem                        there -- which CI has had signed in between
rem
rem The tests run first, unless WINDOWS_TESTS_PASSED is set to 1: that is CI
rem saying its Windows test job has already passed for this commit, which the
rem installer job waits for. Nothing on your own machine sets it.
rem
rem Two notes for editing this file: batch files must keep CRLF line endings or
rem cmd.exe mis-parses labels and goto (.gitattributes enforces it), and
rem "if errorlevel 1 goto :fail" is used rather than "|| goto :fail", which is
rem unreliable after a quoted interpreter invocation.
setlocal
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe
rem The Python the installer is built with, and so the one inside it: the
rem one CI builds releases with, and one every library pycangui ships has a
rem ready-made download for -- the MDF reader had none for 3.15 when it came
rem out. Whatever python is first on the PATH is not chosen by anyone.
set BUILD_PY=3.14
set CHECK_PY=import sys; v = sys.version_info; sys.exit(f'{v.major}.{v.minor}' != '%BUILD_PY%')
if /i "%~1"=="setup" goto :setup

echo.
echo === Environment ===================================================
if exist "%PYTHON%" goto :havevenv
echo Creating the virtual environment in .venv. First time only, and it
echo downloads a couple of hundred megabytes -- Qt is most of it -- so on a
echo slow connection this takes several minutes.
rem The python on the PATH if it is the one, as it is in CI; otherwise the
rem py launcher, asked for it by version.
set "BASE="
python -c "%CHECK_PY%" >nul 2>&1 && set "BASE=python"
if not defined BASE py -%BUILD_PY% -c "%CHECK_PY%" >nul 2>&1 && set "BASE=py -%BUILD_PY%"
if not defined BASE goto :nopython
%BASE% -m venv .venv
if errorlevel 1 goto :fail
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :fail
goto :checkvenv

:havevenv
echo Using the existing .venv.
:checkvenv
rem An existing one too: made earlier, by hand or by another Python.
"%PYTHON%" -c "%CHECK_PY%"
if errorlevel 1 goto :wrongvenv
echo Installing pycangui, its dependencies and PyInstaller...
"%PYTHON%" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail
"%PYTHON%" -m pip install pyinstaller
if errorlevel 1 goto :fail

echo.
echo === Tests =========================================================
if "%WINDOWS_TESTS_PASSED%"=="1" goto :tested
rem -n auto: a thousand Qt tests split cleanly across processes.
"%PYTHON%" -m pytest -q -n auto tests
if errorlevel 1 goto :fail
goto :notices

:tested
echo Already passed on Windows for this commit, so not run again.

:notices

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

:setup
if not exist dist\pycangui\pycangui.exe (
    echo There is no dist\pycangui to package: run build.cmd nosetup first.
    goto :fail
)

rem The version comes from git: a tag is built as the tag, and refused if it
rem disagrees with pycangui.__version__; anything else is a -dev build named
rem after its commit. build\version.py says how. Read through a file: nesting
rem quotes inside a for /f is parsed differently by cmd depending on context,
rem and comes out empty.
set VERSION=
set FILEVERSION=
set VERSIONFILE=%TEMP%\pycangui_version.txt
"%PYTHON%" build\version.py > "%VERSIONFILE%"
if errorlevel 1 goto :fail
set /p VERSION=<"%VERSIONFILE%"
"%PYTHON%" build\version.py --file > "%VERSIONFILE%"
if errorlevel 1 goto :fail
set /p FILEVERSION=<"%VERSIONFILE%"
del "%VERSIONFILE%" 2>nul
if not defined VERSION (
    echo Could not work out the version.
    goto :fail
)

echo.
echo === Installer =====================================================
echo Version %VERSION%
rem Newest first, then whatever is on the PATH, so this keeps working across
rem Inno Setup versions without being edited.
set ISCC=
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%p if not defined ISCC set ISCC=%%p
if not defined ISCC for %%p in (ISCC.exe) do if not "%%~$PATH:p"=="" set ISCC="%%~$PATH:p"
if not defined ISCC (
    echo Inno Setup was not found, so no setup.exe was made.
    echo The application itself is ready in dist\pycangui.
    echo Install it from https://jrsoftware.org/isinfo.php to build one.
    goto :done
)
%ISCC% /DAppVersion=%VERSION% /DFileVersion=%FILEVERSION% build\installer.iss
if errorlevel 1 goto :fail
echo Installer: dist\pycangui-%VERSION%-setup.exe

:done
echo.
echo Application: dist\pycangui\pycangui.exe
exit /b 0

:nopython
echo.
echo The installer is built with Python %BUILD_PY%, and it was not found: not as
echo the python on the PATH, nor through the py launcher. Install it with the
echo Python Install Manager, "py install %BUILD_PY%", and run this again.
exit /b 1

:wrongvenv
echo.
echo .venv was made with a Python other than %BUILD_PY%, which the installer is
echo built with. Delete the .venv folder, and run this again to make it afresh.
exit /b 1

:fail
echo.
echo BUILD FAILED ^(errorlevel %errorlevel%^)
exit /b 1
