"""Lazy exports from the active Qt binding's QtCore module."""

# pyright: reportWildcardImportFromLibrary = false

from typing import TYPE_CHECKING, Any

from ._api import USE_PYSIDE6

if USE_PYSIDE6:
    import PySide6.QtCore as _qt
else:
    import PyQt6.QtCore as _qt

if TYPE_CHECKING:
    if USE_PYSIDE6:
        from PySide6.QtCore import *
    else:
        from PyQt6.QtCore import *
        from PyQt6.QtCore import pyqtSignal, pyqtSlot

        Signal = pyqtSignal
        Slot = pyqtSlot


def __getattr__(name: str) -> Any:
    backend_name = name
    if name == "Signal" and not USE_PYSIDE6:
        backend_name = "pyqtSignal"
    elif name == "Slot" and not USE_PYSIDE6:
        backend_name = "pyqtSlot"
    value = getattr(_qt, backend_name)
    globals()[name] = value
    return value
