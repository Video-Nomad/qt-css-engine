# pyright: reportMissingImports = false
# pyright: reportWildcardImportFromLibrary = false
# ty: ignore[unresolved-import]

from ._api import USE_PYSIDE6

if USE_PYSIDE6:
    from PySide6.QtWidgets import *
else:
    from PyQt6.QtWidgets import *
