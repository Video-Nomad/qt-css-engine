"""Qt binding detection. Import USE_PYSIDE6 to branch on the active binding."""

import importlib.util
import os
import sys

_qt_api = os.environ.get("QT_API", "").lower()
_pyside6_loaded = "PySide6" in sys.modules
_pyqt6_loaded = "PyQt6" in sys.modules

USE_PYSIDE6: bool = (
    _qt_api == "pyside6"
    or (not _qt_api and _pyside6_loaded and not _pyqt6_loaded)
    or (not _qt_api and not _pyside6_loaded and not _pyqt6_loaded and bool(importlib.util.find_spec("PySide6")))
)
