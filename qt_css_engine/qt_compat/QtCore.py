# pyright: reportMissingImports = false
# pyright: reportUnknownVariableType = false
# pyright: reportUnusedImport = false
# pyright: reportWildcardImportFromLibrary = false
# ty: ignore[unresolved-import]

from ._api import USE_PYSIDE6

if USE_PYSIDE6:
    from PySide6.QtCore import *
    from PySide6.QtCore import Signal, Slot  # noqa: F401
else:
    from PyQt6.QtCore import *
    from PyQt6.QtCore import pyqtSignal as Signal  # noqa: F401
    from PyQt6.QtCore import pyqtSlot as Slot  # noqa: F401
