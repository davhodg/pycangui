@echo off
rem Build a distributable pycangui: a PyInstaller one-directory build, checked,
rem then wrapped in a Windows installer with Inno Setup.
rem
rem   build.cmd            build, check, and make setup.exe
rem   build.cmd nosetup    stop after the checked one-directory build
rem
rem Two notes for editing this file: batch files must keep CRLF line endings or
rem cmd.exe mis-parses labels and goto (.gitattributes enforces it), and
rem "if errorlevel 1 goto :fail" is used rather than "|| goto :fail", which is
rem unreliable after a quoted interpreter invocation.
setlocal
cd /d "%~dp0"

set PYTHON=.venv\Scripts\python.exe

echo.
echo === Environment ===================================================
if not exist "%PYTHON%" (
    echo Creating the virtual environment in .venv. First time only, and it
    echo downloads roughly 250 MB of packages -- Qt is most of it -- so on a
    echo slow connection this takes several minutes.
    python -m venv .venv
    if errorlevel 1 goto :fail
    "%PYTHON%" -m pip install --upgrade pip
    if errorlevel 1 goto :fail
) else (
    echo Using the existing .venv.
)
echo Installing pycangui, its dependencies and PyInstaller...
"%PYTHON%" -m pip install -e ".[dev]"
if errorlevel 1 goto :fail
"%PYTHON%" -m pip install pyinstaller
if errorlevel 1 goto :fail

echo.
echo === Tests =========================================================
rem -n auto: a thousand Qt tests split cleanly across processes.
"%PYTHON%" -m pytest -q -n auto tests
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

:fail
echo.
echo BUILD FAILED ^(errorlevel %errorlevel%^)
exit /b 1
