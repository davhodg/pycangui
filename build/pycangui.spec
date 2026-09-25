# PyInstaller specification for pycangui. Build with: build.cmd
#
# Two things need care here, both because PyInstaller works by *reading the
# source* to find imports and files:
#
# * python-can chooses its adapter backend from a string at run time
#   (``can.Bus(interface="kvaser")``), so nothing imports those modules
#   statically and PyInstaller would leave every one of them out. They are
#   listed as hidden imports below, taken from python-can's own table so the
#   list cannot drift.
# * the built-in hook templates are *copied as source* into the user's folder
#   on first run, and the EDS / DBC / A2L samples are read as files. They are
#   collected as data, not code, so they exist on disk in the bundle.
#
# Qt is left as separate DLLs (a one-directory build) so it stays dynamically
# linked and replaceable, which is what the LGPL asks for. GPL-only Qt modules
# are excluded here and the build fails if any of them appear anyway.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

import can.interfaces

PROJECT = Path(SPECPATH).parent

# Every python-can backend module, from python-can's own registry.
CAN_BACKENDS = sorted({module for module, _class in can.interfaces.BACKENDS.values()})

HIDDEN = [
    *CAN_BACKENDS,
    *collect_submodules("can.io"),  # log readers and writers, also chosen by name
    "canopen",
    "cantools",
    "isotp",
    "j1939",
    "udsoncan",
    "bincopy",
    # asammdf reads MDF and MF4 measurement files. pycangui imports it only
    # when a file needs one, so nothing static points at it and PyInstaller
    # would leave it out; and its format blocks are picked by file version at
    # run time, hence sweeping the package rather than naming the entry point.
    "asammdf",
    *collect_submodules("asammdf.blocks"),
    # canmatrix, which asammdf reads databases through, loads its format
    # modules by name from a registry -- the python-can backend pattern again.
    *collect_submodules("canmatrix.formats"),
    # pywin32 is a distribution, not a module: name the modules it provides
    # python-can's usb2can backend finds adapters through win32com.
    "pythoncom",
    "win32com",
    "win32api",
]

# Qt modules that are GPL or commercial only. Shipping one would change the
# licence of the whole application, so they are kept out deliberately.
GPL_QT_MODULES = [
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtVirtualKeyboard",
    "PySide6.QtWaylandCompositor",
]

EXCLUDED = [
    *GPL_QT_MODULES,
    # Qt modules pycangui does not use.
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQml",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialBus",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    # asammdf's own measurement GUI. pycangui has its reader, not its
    # application, and that application wants the Qt addons deliberately left
    # out above -- so it would either add modules the build does not use, or
    # half-import and fail.
    "asammdf.gui",
    "asammdf.app",
    # Test suites that ship inside libraries, which a build never runs.
    "pandas.tests",
    "numpy.tests",
    # developer tooling that has no business in a release build
    "pytest",
    "ruff",
    "PyInstaller",
]

DATA = [
    # Sample device files, read as files at run time.
    (str(PROJECT / "pycangui" / "resources"), "pycangui/resources"),
    (str(PROJECT / "pycangui" / "help"), "pycangui/help"),
    # Hook templates and the shipped virtual nodes: copied *as source* into
    # the user's folder on first run, so they must exist as real files, not
    # only as compiled modules.
    (str(PROJECT / "pycangui" / "hooks"), "pycangui/hooks"),
    (str(PROJECT / "pycangui" / "nodes"), "pycangui/nodes"),
    (str(PROJECT / "LICENSE"), "."),
    # The MIT-0 text the hook and node templates name in their SPDX line.
    (str(PROJECT / "LICENSES"), "LICENSES"),
    (str(PROJECT / "NOTICE"), "."),
    (str(PROJECT / "THIRD-PARTY-NOTICES.txt"), "."),
    (str(PROJECT / "README.md"), "."),
]

#: Qt ships these as plain DLLs and plugins collected by PySide6's own
#: PyInstaller hook, so ``excludes`` (which only filters Python modules) does
#: not remove them. They have to be filtered out of the collected binaries.
GPL_QT_BINARY_TOKENS = (
    "qt6charts",
    "qt6datavisualization",
    "qt6graphs",
    "qt6virtualkeyboard",
    "qt6waylandcompositor",
    "virtualkeyboardplugin",
)


def _is_gpl_qt(entry) -> bool:
    name = Path(entry[0]).name.lower()
    return any(token in name for token in GPL_QT_BINARY_TOKENS)


analysis = Analysis(
    [str(PROJECT / "pycangui" / "__main__.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=DATA,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDED,
    noarchive=False,
)

analysis.binaries = TOC(e for e in analysis.binaries if not _is_gpl_qt(e))
analysis.datas = TOC(e for e in analysis.datas if not _is_gpl_qt(e))

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="pycangui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX compression is a reliable way to be flagged by antivirus
    console=False,  # a GUI application: no console window
    # Explorer, the Start menu and the desktop shortcut read the icon out of
    # the exe, not from the running window. Regenerate with build/icon.py.
    icon=str(PROJECT / "pycangui" / "resources" / "pycangui.ico"),
)

COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="pycangui",
)
