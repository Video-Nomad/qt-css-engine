"""Lazy exports from the active Qt binding's QtWidgets module."""

# pyright: reportWildcardImportFromLibrary = false

from typing import TYPE_CHECKING, Any

from ._api import USE_PYSIDE6

if USE_PYSIDE6:
    import PySide6.QtWidgets as _qt
else:
    import PyQt6.QtWidgets as _qt

if TYPE_CHECKING:
    if USE_PYSIDE6:
        from PySide6.QtWidgets import *
    else:
        from PyQt6.QtWidgets import *


def __getattr__(name: str) -> Any:
    value = getattr(_qt, name)
    globals()[name] = value
    return value
