"""Work around can-j1939 2.0.x calling ``pythoncom.CoUnitialize()`` (a typo
for ``CoUninitialize``) when its job thread exits on Windows, which otherwise
raises an AttributeError in that thread every time an ECU is stopped."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    try:
        import pythoncom

        if not hasattr(pythoncom, "CoUnitialize"):
            pythoncom.CoUnitialize = pythoncom.CoUninitialize
    except ImportError:  # pywin32 missing: the library will fail loudly on its own
        pass
